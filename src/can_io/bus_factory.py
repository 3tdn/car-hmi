"""Factory tạo các instance ``can.BusABC`` từ AppConfig."""

from __future__ import annotations

import logging
from typing import Any

import can

from src.core.config import CANConfig

logger = logging.getLogger(__name__)


def create_bus(cfg: CANConfig, **kwargs: Any) -> can.BusABC:
    """Tạo và trả về instance ``can.Bus`` từ cấu hình CAN đã cho.

    Hỗ trợ tất cả giao diện mà python-can cung cấp:
    virtual, socketcan, pcan, vector, kvaser, serial, ixxat, …

    Tham số:
        cfg:    Phần ``CANConfig`` từ ``AppConfig``.
        kwargs: Tham số từ khóa được chuyển tiếp tới ``can.Bus()`` (đè lên giá trị cfg).

    Kết quả:
        Một instance ``can.Bus`` đã mở.

    Ngoại lệ:
        can.CanInterfaceNotImplementedError: nếu giao diện không khảdụng.
        can.CanInitializationError: nếu không mở được bus (thiếu phần cứng, v.v.).
    """
    params: dict[str, Any] = {
        "interface": cfg.interface,
        "channel": cfg.channel,
        "bitrate": cfg.bitrate,
    }
    params.update(kwargs)

    # virtualbus không cần tham số bitrate
    if params["interface"] == "virtual":
        params.pop("bitrate", None)

    logger.info(
        "Opening CAN bus: interface=%s channel=%s bitrate=%s",
        params["interface"],
        params["channel"],
        params.get("bitrate", "n/a"),
    )
    bus = can.Bus(**params)
    logger.info("CAN bus opened: %s", bus)
    return bus


def create_virtual_bus(channel: str = "vcan0") -> can.BusABC:
    """Factory tiện lợi tạo bus CAN ảo (không cần phần cứng)."""
    return can.Bus(interface="virtual", channel=channel)
