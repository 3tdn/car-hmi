"""Factory that creates ``can.BusABC`` instances from AppConfig."""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any

import can

from src.can_io.parser import DatabaseLoader
from src.core.config import CANConfig

logger = logging.getLogger(__name__)

_SOCKETCAN_ARPHRD_TYPE = 280
_IFF_UP = 0x1
_AUTO_PROBE_TIMEOUT_SEC = 3.0
_AUTO_PROBE_SLICE_SEC = 0.05


def _natural_channel_key(channel: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", channel)]


def resolve_auto_match_ids(cfg: CANConfig, db: DatabaseLoader) -> set[int]:
    """Resolve discovery signals to message IDs without widening an explicit selection."""
    if not cfg.channel_tracking_signals:
        return {msg_id for msg_id, message in db.messages.items() if message.signals}

    match_ids: set[int] = set()
    for signal in cfg.channel_tracking_signals:
        message = db.get_message_for_signal(signal)
        if message is None:
            raise ValueError(
                f"Unknown channel_tracking_signals signal '{signal}' in {cfg.can_db_file}"
            )
        match_ids.add(message.msg_id)
    return match_ids


def list_socketcan_channel_devices(
    sys_class_net: Path = Path("/sys/class/net"),
) -> list[dict[str, str]]:
    """Return Linux CAN interfaces with administrative and operational state."""
    try:
        interfaces = list(sys_class_net.iterdir())
    except FileNotFoundError:
        logger.debug("SocketCAN sysfs directory is unavailable: %s", sys_class_net)
        return []
    except OSError as exc:
        logger.warning("Cannot inspect SocketCAN interfaces in %s: %s", sys_class_net, exc)
        return []

    devices: list[dict[str, str]] = []
    for interface_path in interfaces:
        try:
            hardware_type = int((interface_path / "type").read_text().strip(), 0)
        except (OSError, ValueError):
            continue
        if hardware_type != _SOCKETCAN_ARPHRD_TYPE:
            continue

        try:
            flags = int((interface_path / "flags").read_text().strip(), 0)
            state = "up" if flags & _IFF_UP else "down"
        except (OSError, ValueError):
            state = "unknown"
        try:
            operstate = (interface_path / "operstate").read_text().strip().lower() or "unknown"
        except OSError:
            operstate = "unknown"

        devices.append(
            {
                "channel": interface_path.name,
                "interface": "socketcan",
                "state": state,
                "operstate": operstate,
            }
        )

    return sorted(devices, key=lambda device: _natural_channel_key(device["channel"]))


def list_up_socketcan_channels(
    sys_class_net: Path = Path("/sys/class/net"),
) -> list[str]:
    """Return naturally sorted Linux CAN network interfaces whose IFF_UP flag is set."""
    return [
        device["channel"]
        for device in list_socketcan_channel_devices(sys_class_net)
        if device["state"] == "up"
    ]


def _exact_can_filters(match_ids: set[int]) -> list[dict[str, int | bool]]:
    return [
        {
            "can_id": msg_id,
            "can_mask": 0x1FFFFFFF,
            "extended": msg_id > 0x7FF,
        }
        for msg_id in sorted(match_ids)
    ]


def _create_auto_socketcan_bus(
    *,
    bitrate: int,
    match_ids: set[int],
    probe_timeout_sec: float,
    sys_class_net: Path,
) -> can.BusABC:
    """Select an UP SocketCAN interface that receives a CAN ID from the channel DBC."""
    if not match_ids:
        raise can.CanInitializationError(
            "channel='auto' requires at least one DBC message containing a signal"
        )

    channels = list_up_socketcan_channels(sys_class_net)
    if not channels:
        raise can.CanInitializationError("No UP SocketCAN interface is available")

    opened: list[tuple[str, can.BusABC]] = []
    open_errors: list[str] = []
    filters = _exact_can_filters(match_ids)
    selected_bus: can.BusABC | None = None
    try:
        for channel in channels:
            try:
                opened.append(
                    (
                        channel,
                        can.Bus(
                            interface="socketcan",
                            channel=channel,
                            bitrate=bitrate,
                            can_filters=filters,
                        ),
                    )
                )
            except can.CanError as exc:
                open_errors.append(f"{channel}: {exc}")
                logger.warning("Cannot probe SocketCAN channel '%s': %s", channel, exc)

        if not opened:
            detail = "; ".join(open_errors) or "all candidates failed to open"
            raise can.CanInitializationError(f"Cannot open any UP SocketCAN interface ({detail})")

        deadline = time.monotonic() + max(0.0, probe_timeout_sec)
        active = list(opened)
        while active and time.monotonic() < deadline:
            next_active: list[tuple[str, can.BusABC]] = []
            for channel, bus in active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    msg = bus.recv(timeout=min(_AUTO_PROBE_SLICE_SEC, remaining))
                except can.CanError as exc:
                    logger.warning("SocketCAN probe failed on '%s': %s", channel, exc)
                    # Do not keep polling a broken socket. Some SocketCAN
                    # failures return immediately, which otherwise creates a
                    # tight CPU/logging loop for the rest of the probe window.
                    continue
                next_active.append((channel, bus))
                if msg is None or msg.arbitration_id not in match_ids:
                    continue

                # Discovery filters must not limit normal reader traffic.
                bus.set_filters(None)
                selected_bus = bus
                # Preserve the frame used for validation so the reader still
                # decodes it. This matters when the matching DBC message is a
                # one-shot event rather than periodic traffic.
                bus._car_hmi_prefetched_message = msg
                logger.info(
                    "Auto-selected SocketCAN channel '%s' after receiving DBC message %#x",
                    channel,
                    msg.arbitration_id,
                )
                return bus
            active = next_active

        if not active:
            raise can.CanInitializationError(
                "All UP SocketCAN interfaces failed while probing for DBC traffic"
            )

        raise can.CanInitializationError(
            "No UP SocketCAN interface received a message from the configured DBC "
            f"within {probe_timeout_sec:.1f}s (candidates: {', '.join(channels)})"
        )
    finally:
        for _, bus in opened:
            if bus is selected_bus:
                continue
            try:
                bus.shutdown()
            except Exception:
                logger.debug("Failed to close an unselected SocketCAN probe bus", exc_info=True)


def create_bus(
    cfg: CANConfig,
    *,
    auto_match_ids: set[int] | None = None,
    auto_probe_timeout_sec: float = _AUTO_PROBE_TIMEOUT_SEC,
    socketcan_sysfs: Path = Path("/sys/class/net"),
    **kwargs: Any,
) -> can.BusABC:
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

    if params["channel"] == "auto":
        if params["interface"] != "socketcan":
            raise can.CanInitializationError(
                "channel='auto' is supported only with interface='socketcan'"
            )
        logger.info(
            "Discovering an UP SocketCAN interface with traffic matching %d DBC message(s)",
            len(auto_match_ids or set()),
        )
        return _create_auto_socketcan_bus(
            bitrate=int(params["bitrate"]),
            match_ids=auto_match_ids or set(),
            probe_timeout_sec=auto_probe_timeout_sec,
            sys_class_net=socketcan_sysfs,
        )

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
