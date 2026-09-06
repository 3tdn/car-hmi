"""Factory that creates ``can.BusABC`` instances from AppConfig."""

from __future__ import annotations

import logging
from typing import Any

import can

from src.core.config import CANConfig

logger = logging.getLogger(__name__)


def create_bus(cfg: CANConfig, **kwargs: Any) -> can.BusABC:
    """Create and return a ``can.Bus`` instance from the given CAN configuration.

    Supports all interfaces provided by python-can:
    virtual, socketcan, pcan, vector, kvaser, serial, ixxat, …

    Args:
        cfg:    The ``CANConfig`` section from ``AppConfig``.
        kwargs: Keyword arguments forwarded to ``can.Bus()`` (overriding cfg values).

    Returns:
        An open ``can.Bus`` instance.

    Raises:
        can.CanInterfaceNotImplementedError: if the interface is unavailable.
        can.CanInitializationError: if the bus cannot be opened (missing hardware, etc.).
    """
    params: dict[str, Any] = {
        "interface": cfg.interface,
        "channel": cfg.channel,
        "bitrate": cfg.bitrate,
    }
    params.update(kwargs)

    # A virtual bus does not require a bitrate parameter
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
    """Convenience factory for creating a virtual CAN bus (no hardware required)."""
    return can.Bus(interface="virtual", channel=channel)
