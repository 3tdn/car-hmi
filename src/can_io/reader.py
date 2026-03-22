"""Bộ đọc khung CAN bất đồng bộ hỗ trợ giải mã đầy đủ (DBC / CANdb JSON / A2L)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import can

from src.can_io.parser import DatabaseLoader

logger = logging.getLogger(__name__)


@dataclass
class RawCANFrame:
    timestamp: float
    bus: str
    msg_id: int
    is_extended: bool
    is_fd: bool
    data: bytes


@dataclass
class DecodedFrame:
    raw: RawCANFrame
    signals: dict[str, float] = field(default_factory=dict)
    msg_name: str = ""


class CANReader:
    """Bất đồng bộ đọc khung CAN, giải mã qua DatabaseLoader.

    Khung đã giải mã được đưa vào asyncio.Queue cho SignalPipeline.
    Hỗ trợ:
    - Bộ lọc danh sách cho phép theo CAN ID (tùy chọn)
    - Tự kết nối lại khi lỗi bus (backoff mũ  1 s → 30 s)
    - Callback mở lại bus sạch sẽ khi kết nối lại
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        queue: asyncio.Queue[DecodedFrame],
        filter_ids: set[int] | None = None,
        bus_factory: Callable[[], can.BusABC] | None = None,
        max_reconnect_retries: int = 5,
        queue_policy: str = "reject",
    ) -> None:
        """
        Tham số:
            bus:            Đối tượng ``can.Bus`` đã mở.
            db:             ``DatabaseLoader`` đã tải các định nghĩa thông điệp/tín hiệu.
            queue:          Hàng đợi đầu ra cho khung đã giải mã.
            filter_ids:     Nếu không rỗng, chỉ xử lý khung có những ID này.
            bus_factory:    Callable để mở lại bus khi mất kết nối (tùy chọn).
            max_reconnect_retries: Số lần thử kết nối lại tối đa liên tiếp.
        """
        self._bus = bus
        self._db = db
        self._queue = queue
        self._filter_ids = filter_ids or set()
        self._bus_factory = bus_factory
        self._max_retries = max_reconnect_retries
        self._policy = queue_policy
        self._running = False
        self._error_count = 0
        self._dropped_count = 0

    async def start(self) -> None:
        """Bắt đầu vòng lặp đọc bất đồng bộ — chạy đến khi ``stop()`` được gọi."""
        self._running = True
        logger.info(
            "CAN Reader started (interface=%s, channel=%s, %d msgs in DB)",
            getattr(self._bus, "INTERFACE", "?"),
            getattr(self._bus, "channel", "?"),
            len(self._db.messages),
        )
        loop = asyncio.get_running_loop()
        while self._running:
            try:
                msg: can.Message | None = await loop.run_in_executor(
                    None, lambda: self._bus.recv(timeout=1.0)
                )
                if msg is None:
                    continue  # hết thời gian chờ — kiểm tra _running và tiếp tục
                if self._filter_ids and msg.arbitration_id not in self._filter_ids:
                    continue
                frame = self._decode(msg)
                if not frame.signals:
                    logger.debug(
                        "Decoded frame msg_id=%#x has no signals (DB may not contain this message)",
                        msg.arbitration_id,
                    )
                # Enforce configured queue policy when the pipeline queue is full
                try:
                    if self._policy == "block":
                        await self._queue.put(frame)
                    else:
                        # reject or drop_oldest handled via non-blocking put
                        self._queue.put_nowait(frame)
                except asyncio.QueueFull:
                    # Track dropped frames for metrics
                    self._dropped_count += 1
                    if self._policy == "drop_oldest":
                        try:
                            # Remove oldest item and try again
                            _ = self._queue.get_nowait()
                            self._queue.put_nowait(frame)
                            logger.debug("RX queue full — dropped oldest frame and enqueued new (msg_id=%#x)", msg.arbitration_id)
                        except Exception:
                            logger.warning("RX queue full (msg_id=%#x) — frame dropped after drop_oldest attempt", msg.arbitration_id)
                    else:
                        # reject (default) — drop this incoming frame
                        logger.warning("RX queue full (msg_id=%#x) — frame dropped", msg.arbitration_id)

            except can.CanError as exc:
                self._error_count += 1
                logger.error(
                    "CAN bus error #%d (ERR_CAN_BUS_OFF): %s — reconnecting...",
                    self._error_count,
                    exc,
                )
                await self._reconnect()
            except Exception as exc:
                logger.exception("Unexpected error in CAN read loop: %s", exc)
    def stop(self) -> None:
        """Thông báo cho vòng lặp đọc dừng sạch sẽ."""
        self._running = False

    # ── Giải mã ───────────────────────────────────────────────────────────────

    def _decode(self, msg: can.Message) -> DecodedFrame:
        raw = RawCANFrame(
            timestamp=msg.timestamp if msg.timestamp else time.time(),
            bus=getattr(self._bus, "channel", "unknown"),
            msg_id=msg.arbitration_id,
            is_extended=msg.is_extended_id,
            is_fd=msg.is_fd,
            data=bytes(msg.data),
        )
        signals = self._db.decode_frame(msg.arbitration_id, bytes(msg.data))
        msg_def = self._db.messages.get(msg.arbitration_id)
        return DecodedFrame(
            raw=raw,
            signals=signals,
            msg_name=msg_def.name if msg_def else "",
        )

    # ── Kết nối lại ───────────────────────────────────────────────────────────

    async def _reconnect(self, max_retries: int | None = None) -> None:
        """Thử mở lại bus CAN với backoff mũ (1 s → 30 s)."""
        retries = max_retries if max_retries is not None else self._max_retries
        for attempt in range(1, retries + 1):
            delay = min(2 ** (attempt - 1), 30)
            logger.info("Reconnect attempt %d/%d in %d s...", attempt, retries, delay)
            await asyncio.sleep(delay)
            try:
                self._bus.shutdown()
                if self._bus_factory:
                    self._bus = self._bus_factory()
                    logger.info("CAN bus re-opened: %s", self._bus)
                else:
                    logger.warning("No bus_factory — cannot re-open bus; stopping reader")
                    self.stop()
                return
            except can.CanError as exc:
                logger.warning("Reconnect attempt %d failed: %s", attempt, exc)
        logger.critical(
            "CAN bus reconnect failed after %d attempts — supervisor must intervene",
            retries,
        )
        self.stop()  # dừng vòng lặp; watchdog/supervisor phải khởi động lại tiến trình

    def get_metrics(self) -> dict:
        """Trả về metrics nội bộ của reader (dropped frames, error count)."""
        return {"dropped_frames": int(self._dropped_count), "error_count": int(self._error_count)}

    # Runtime helpers
    def set_queue_policy(self, policy: str) -> None:
        """Cập nhật chính sách hàng đợi tại runtime."""
        self._policy = policy

    def set_queue(self, queue: asyncio.Queue) -> None:
        """Hoán đổi tham chiếu đến hàng đợi đầu ra (caller chịu trách nhiệm về chuyển dữ liệu)."""
        self._queue = queue
