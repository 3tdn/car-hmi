"""Mã hóa giá trị tín hiệu thành khung CAN và gửi trên bus."""

from __future__ import annotations

import asyncio
import logging
import time

import can

from src.can_io.parser import DatabaseLoader

logger = logging.getLogger(__name__)


class CANWriter:
    """Mã hóa giá trị tín hiệu → khung CAN và gửi trên bus.

    Tất cả hành động ghi được nối tiếp qua ``asyncio.Lock`` để tránh
    truyền khung đồng thời từ nhiều caller bất đồng bộ.
    """

    def __init__(self, bus: can.BusABC, db: DatabaseLoader) -> None:
        """
        Tham số:
            bus: Đối tượng ``can.Bus`` mở để ghi vào.
            db:  ``DatabaseLoader`` dùng để mã hóa tín hiệu.
        """
        self._bus = bus
        self._db = db
        self._lock = asyncio.Lock()
        self._sent_count = 0

    async def send_signal(self, name: str, value: float) -> None:
        """Mã hóa một tín hiệu và truyền khung CAN tương ứng.

        Tham số:
            name:  Tên tín hiệu theo định nghĩa trong cơ sở dữ liệu DBC/CANdb.
            value: Giá trị vật lý (đơn vị kỹ thuật).

        Ngoại lệ:
            ValueError: nếu tín hiệu ``name`` không tìm thấy trong DB.
            can.CanError: nếu ``bus.send()`` thất bại.
        """
        msg = self._db.encode_signal(name, value)
        if msg is None:
            raise ValueError(f"Signal '{name}' not found in CAN database — cannot encode")
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
