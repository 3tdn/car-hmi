"""Mã hóa giá trị tín hiệu thành khung CAN và gửi trên bus."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import can

from src.can_io.parser import DatabaseLoader

if TYPE_CHECKING:
    from src.core.config import WriterConfig
    from src.core.signal_store import SignalStore

logger = logging.getLogger(__name__)


class CANWriter:
    """Mã hóa giá trị tín hiệu → khung CAN và gửi trên bus.

    Tất cả hành động ghi được nối tiếp qua ``asyncio.Lock`` để tránh
    truyền khung đồng thời từ nhiều caller bất đồng bộ.

    Nếu ``signal_store`` được cung cấp, ``send_signal`` sẽ:
    1. Đọc giá trị hiện tại của các tín hiệu cùng message (read-modify-write)
       để không zero-out các tín hiệu khác trong cùng CAN frame.
    2. Cập nhật SignalStore trực tiếp sau khi gửi thành công, vì SocketCAN
       mặc định không loopback lại frame của chính socket (recv_own_msgs=False).

    Khi ``periodic_mode=True`` (từ WriterConfig), mỗi lần ``send_signals_batch``
    sẽ gửi ngay lập tức, sau đó tiếp tục gửi lại theo chu kỳ ``periodic_time_step`` ms
    trong tổng thời gian ``periodic_duration`` ms.  Rate-limit và burst bị bỏ qua.
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        signal_store: "SignalStore | None" = None,
        writer_config: "WriterConfig | None" = None,
    ) -> None:
        """
        Tham số:
            bus:           Đối tượng ``can.Bus`` mở để ghi vào.
            db:            ``DatabaseLoader`` dùng để mã hóa tín hiệu.
            signal_store:  Tham chiếu tới SignalStore để read-modify-write và
                           cập nhật dashboard sau khi gửi (tuỳ chọn).
            writer_config: Cấu hình writer (periodic mode, rate limit, ...).
        """
        self._bus = bus
        self._db = db
        self._store = signal_store
        self._lock = asyncio.Lock()
        self._sent_count = 0

        # Periodic mode config
        self._set_writer_config(writer_config)

        # Quản lý periodic tasks: msg_id → asyncio.Task
        self._periodic_tasks: dict[int, asyncio.Task] = {}

    def _set_writer_config(self, writer_config: "WriterConfig | None") -> None:
        if writer_config is not None:
            self._periodic_mode = writer_config.periodic_mode
            self._periodic_time_step_ms = writer_config.periodic_time_step
            self._periodic_duration_ms = writer_config.periodic_duration
        else:
            self._periodic_mode = False
            self._periodic_time_step_ms = 20
            self._periodic_duration_ms = 10000

    def apply_runtime_config(self, writer_config: "WriterConfig | None") -> None:
        """Update periodic writer behavior without recreating the writer instance."""
        self._set_writer_config(writer_config)
        for msg_id, task in list(self._periodic_tasks.items()):
            if task is not None and not task.done():
                task.cancel()
                self._periodic_tasks.pop(msg_id, None)

    async def send_signal(self, name: str, value: float) -> None:
        """Mã hóa một tín hiệu và truyền khung CAN tương ứng.

        Delegate sang ``send_signals_batch`` để dùng chung logic
        read-modify-write (giữ nguyên các tín hiệu khác cùng message).

        Tham số:
            name:  Tên tín hiệu theo định nghĩa trong cơ sở dữ liệu DBC/CANdb.
            value: Giá trị vật lý (đơn vị kỹ thuật).

        Ngoại lệ:
            ValueError: nếu tín hiệu ``name`` không tìm thấy trong DB.
            can.CanError: nếu ``bus.send()`` thất bại.
        """
        await self.send_signals_batch({name: value})

    async def send_signals_batch(self, signals: dict[str, float]) -> dict[str, float]:
        """Gộp nhiều tín hiệu theo message ID rồi gửi mỗi message một frame duy nhất.

        Với mỗi CAN message được đề cập trong ``signals``:
        - Đọc giá trị hiện tại của tất cả tín hiệu còn lại trong message từ
          SignalStore (read-modify-write) để không zero-out chúng.
        - Ghi đè bằng các giá trị mới trong ``signals``.
        - Mã hoá và gửi một frame CAN duy nhất cho message đó.

        Tham số:
            signals: dict {signal_name → giá_trị_vật_lý} cho tất cả tín hiệu cần ghi.

        Trả về:
            dict {signal_name → value} của các tín hiệu đã được gửi thành công.

        Ngoại lệ:
            ValueError: nếu một tín hiệu không tìm thấy trong DB của kênh này.
        """
        # ── Bước 1: gom nhóm theo message ─────────────────────────────────────
        from src.can_io.parser import ParsedMessage  # tránh circular ở top-level

        msg_groups: dict[int, dict[str, float]] = {}
        msg_defs: dict[int, ParsedMessage] = {}

        for sig_name, value in signals.items():
            msg_def = self._db.get_message_for_signal(sig_name)
            if msg_def is None:
                raise ValueError(
                    f"Signal '{sig_name}' not found in CAN database — cannot encode"
                )
            if msg_def.msg_id not in msg_groups:
                msg_groups[msg_def.msg_id] = {}
                msg_defs[msg_def.msg_id] = msg_def
            msg_groups[msg_def.msg_id][sig_name] = value

        # ── Bước 2: gửi một frame duy nhất cho mỗi message ────────────────────
        sent: dict[str, float] = {}
        ts = time.time()

        for msg_id, sig_values in msg_groups.items():
            msg_def = msg_defs[msg_id]
            await self._send_frame(msg_id, msg_def, sig_values, ts)

            if self._periodic_mode:
                # Hủy periodic task cũ (nếu có) cho msg_id này
                old_task = self._periodic_tasks.pop(msg_id, None)
                if old_task is not None and not old_task.done():
                    old_task.cancel()
                # Khởi động periodic task mới
                task = asyncio.create_task(
                    self._periodic_sender(msg_id, msg_def, dict(sig_values)),
                    name=f"periodic-writer-{msg_id:#x}",
                )
                self._periodic_tasks[msg_id] = task

            sent.update(sig_values)

        # ── Bước 3: cập nhật SignalStore (fire-and-forget) ───────────────────
        # Tách khỏi await chain để response HTTP trả về ngay sau khi CAN frame
        # đã được gửi, không bị block bởi WS broadcast.
        if self._store is not None and sent:
            asyncio.create_task(self._store.bulk_update(sent, timestamp=ts))

        return sent

    async def _send_frame(
        self,
        msg_id: int,
        msg_def: object,
        sig_values: dict[str, float],
        ts: float,
    ) -> None:
        """Thực hiện read-modify-write rồi gửi một CAN frame cho ``msg_id``."""
        signals_to_encode: dict[str, float] = {}
        if self._store is not None:
            for sig_name in msg_def.signals:
                if sig_name in sig_values:
                    continue
                sv = await self._store.get(sig_name)
                if sv is not None:
                    signals_to_encode[sig_name] = sv.value

        signals_to_encode.update(sig_values)

        msg = self._db.encode_message(msg_id, signals_to_encode)
        if msg is None:
            raise ValueError(f"Failed to encode message {msg_id:#x}")
        msg.timestamp = ts

        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._bus.send, msg)
            except Exception as exc:
                raise can.CanError(
                    f"Failed to send CAN frame for msg_id={msg_id:#x}: {exc}"
                ) from exc
            self._sent_count += 1
            logger.info(
                "CAN batch write [%d]: msg_id=%#x signals=%s data=%s",
                self._sent_count,
                msg_id,
                list(sig_values.keys()),
                msg.data.hex(),
            )

    async def _periodic_sender(
        self,
        msg_id: int,
        msg_def: object,
        sig_values: dict[str, float],
    ) -> None:
        """Gửi lặp lại CAN frame mỗi ``periodic_time_step`` ms trong ``periodic_duration`` ms."""
        interval = self._periodic_time_step_ms / 1000.0
        deadline = time.monotonic() + self._periodic_duration_ms / 1000.0
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(interval)
                if time.monotonic() >= deadline:
                    break
                ts = time.time()
                await self._send_frame(msg_id, msg_def, sig_values, ts)
                if self._store is not None:
                    await self._store.bulk_update(sig_values, timestamp=ts)
        except asyncio.CancelledError:
            logger.debug("Periodic sender cancelled for msg_id=%#x", msg_id)
        finally:
            self._periodic_tasks.pop(msg_id, None)
            logger.debug(
                "Periodic sender stopped for msg_id=%#x after %.1f ms",
                msg_id,
                self._periodic_duration_ms,
            )

    async def send_message(self, msg_id: int, signals: dict[str, float]) -> None:
        """Mã hóa toàn bộ thông điệp theo ID và gửi đi.

        Tham số:
            msg_id:  ID phân xử lý CAN.
            signals: Dict {signal_name: giá_trị_vật_lý} cho tất cả tín hiệu cần mã hóa.

        Ngoại lệ:
            ValueError: nếu ``msg_id`` không tìm thấy trong DB.
            can.CanError: khi gửi thất bại.
        """
        msg = self._db.encode_message(msg_id, signals)
        if msg is None:
            raise ValueError(f"Message ID {msg_id:#x} not found in CAN database — cannot encode")
        msg.timestamp = time.time()
        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._bus.send, msg)
            except Exception as exc:
                raise can.CanError(
                    f"Failed to send CAN message {msg_id:#x}: {exc}"
                ) from exc
            self._sent_count += 1
            logger.debug(
                "CAN write msg [%d]: msg_id=%#x signals=%s",
                self._sent_count,
                msg_id,
                list(signals.keys()),
            )


class CANWriterRouter:
    """Định tuyến yêu cầu ghi tín hiệu đến đúng CANWriter theo kênh.

    Xây dựng bảng ánh xạ O(1): signal_name → CANWriter, msg_id → CANWriter
    để tránh tìm kiếm tuyến tính khi ghi.
    """

    def __init__(self) -> None:
        self._signal_to_writer: dict[str, CANWriter] = {}
        self._msgid_to_writer: dict[int, CANWriter] = {}
        self._writers: list[CANWriter] = []

    def register(self, db: DatabaseLoader, writer: CANWriter) -> None:
        """Đăng ký một CANWriter cùng DatabaseLoader tương ứng."""
        self._writers.append(writer)
        for sig_name in db.signals:
            if sig_name in self._signal_to_writer:
                logger.debug(
                    "CANWriterRouter: signal '%s' already registered "
                    "on another channel — keeping first registration",
                    sig_name,
                )
                continue
            self._signal_to_writer[sig_name] = writer
        for msg_id in db.messages:
            if msg_id in self._msgid_to_writer:
                logger.debug(
                    "CANWriterRouter: msg_id %#x already registered "
                    "on another channel — keeping first registration",
                    msg_id,
                )
                continue
            self._msgid_to_writer[msg_id] = writer

    async def send_signal(self, name: str, value: float) -> None:
        """Định tuyến và gửi tín hiệu qua đúng kênh CAN."""
        writer = self._signal_to_writer.get(name)
        if writer is None:
            raise ValueError(
                f"Signal '{name}' not found in any CAN channel — cannot encode"
            )
        await writer.send_signal(name, value)

    async def send_message(self, msg_id: int, signals: dict[str, float]) -> None:
        """Định tuyến và gửi thông điệp qua đúng kênh CAN."""
        writer = self._msgid_to_writer.get(msg_id)
        if writer is None:
            raise ValueError(
                f"Message ID {msg_id:#x} not found in any CAN channel — cannot encode"
            )
        await writer.send_message(msg_id, signals)

    async def send_signals_batch(
        self, signals: dict[str, float]
    ) -> tuple[dict[str, float], list[dict]]:
        """Gộp batch tín hiệu theo kênh rồi gửi, mỗi CAN message chỉ một frame.

        Tín hiệu không tìm thấy trên bất kỳ kênh nào được thu thập vào danh sách
        lỗi thay vì ném ngoại lệ, để các tín hiệu hợp lệ vẫn được gửi.

        Tham số:
            signals: dict {canonical_signal_name → giá_trị_vật_lý}

        Trả về:
            (sent, errors)
            - sent:   dict {signal_name → value} các tín hiệu đã gửi thành công
            - errors: list[{"signal_name": ..., "error": ...}] các tín hiệu thất bại
        """
        # ── Phân loại signal → writer ──────────────────────────────────────────
        writer_groups: dict[int, tuple[CANWriter, dict[str, float]]] = {}
        errors: list[dict] = []

        for sig_name, value in signals.items():
            writer = self._signal_to_writer.get(sig_name)
            if writer is None:
                errors.append(
                    {
                        "signal_name": sig_name,
                        "error": f"Signal '{sig_name}' not found in any CAN channel",
                    }
                )
                continue
            wid = id(writer)
            if wid not in writer_groups:
                writer_groups[wid] = (writer, {})
            writer_groups[wid][1][sig_name] = value

        # ── Gửi batch cho từng kênh ────────────────────────────────────────────
        sent: dict[str, float] = {}
        for writer, sig_map in writer_groups.values():
            try:
                result = await writer.send_signals_batch(sig_map)
                sent.update(result)
            except ValueError as exc:
                # Đưa toàn bộ tín hiệu của kênh này vào errors
                for sig_name in sig_map:
                    errors.append({"signal_name": sig_name, "error": str(exc), "kind": "value"})
            except can.CanError as exc:
                for sig_name in sig_map:
                    errors.append({"signal_name": sig_name, "error": str(exc), "kind": "transport"})
            except Exception as exc:
                for sig_name in sig_map:
                    errors.append({"signal_name": sig_name, "error": str(exc), "kind": "unknown"})

        return sent, errors
