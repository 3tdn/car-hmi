"""Mã hóa giá trị tín hiệu thành khung CAN và gửi trên bus."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import can

from src.can_io.parser import DatabaseLoader

if TYPE_CHECKING:
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
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        signal_store: "SignalStore | None" = None,
    ) -> None:
        """
        Tham số:
            bus:          Đối tượng ``can.Bus`` mở để ghi vào.
            db:           ``DatabaseLoader`` dùng để mã hóa tín hiệu.
            signal_store: Tham chiếu tới SignalStore để read-modify-write và
                          cập nhật dashboard sau khi gửi (tuỳ chọn).
        """
        self._bus = bus
        self._db = db
        self._store = signal_store
        self._lock = asyncio.Lock()
        self._sent_count = 0

    async def send_signal(self, name: str, value: float) -> None:
        """Mã hóa một tín hiệu và truyền khung CAN tương ứng.

        Thực hiện read-modify-write: đọc giá trị hiện tại của tất cả tín hiệu
        trong cùng CAN message từ SignalStore, sau đó encode full frame để
        tránh zero-out các tín hiệu khác cùng message.

        Tham số:
            name:  Tên tín hiệu theo định nghĩa trong cơ sở dữ liệu DBC/CANdb.
            value: Giá trị vật lý (đơn vị kỹ thuật).

        Ngoại lệ:
            ValueError: nếu tín hiệu ``name`` không tìm thấy trong DB.
            can.CanError: nếu ``bus.send()`` thất bại.
        """
        msg_def = self._db.get_message_for_signal(name)
        if msg_def is None:
            raise ValueError(f"Signal '{name}' not found in CAN database — cannot encode")

        # ── Read-modify-write: giữ nguyên các tín hiệu khác trong cùng frame ──
        signals_to_encode: dict[str, float] = {name: value}
        if self._store is not None:
            for sig_name in msg_def.signals:
                if sig_name == name:
                    continue
                sv = await self._store.get(sig_name)
                if sv is not None:
                    signals_to_encode[sig_name] = sv.value

        msg = self._db.encode_message(msg_def.msg_id, signals_to_encode)
        if msg is None:
            raise ValueError(f"Failed to encode message for signal '{name}'")
        msg.timestamp = time.time()

        async with self._lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._bus.send, msg)
            self._sent_count += 1
            logger.info(
                "CAN write [%d]: %s = %s  (msg_id=%#x, data=%s)",
                self._sent_count,
                name,
                value,
                msg.arbitration_id,
                msg.data.hex(),
            )

        # ── Cập nhật SignalStore trực tiếp sau khi gửi thành công ─────────────
        # SocketCAN mặc định không loopback frame gửi đi về chính socket đó,
        # nên nếu chỉ dựa vào CANReader thì SignalStore/dashboard sẽ không
        # cập nhật. Cập nhật trực tiếp đảm bảo WebSocket clients thấy giá trị mới.
        if self._store is not None:
            await self._store.bulk_update({name: value}, timestamp=time.time())

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
            await loop.run_in_executor(None, self._bus.send, msg)
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
