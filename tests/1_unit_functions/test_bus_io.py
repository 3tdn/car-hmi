"""Tests for bus_factory, CANReader, CANWriter with virtual bus."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import can
import pytest

from src.can_io.bus_factory import create_bus, create_virtual_bus
from src.can_io.parser import DatabaseLoader
from src.can_io.reader import CANReader, DecodedFrame
from src.can_io.writer import CANWriter
from src.core.config import CANConfig


@pytest.fixture
def virtual_bus_pair():
    """Create a pair of virtual buses on the same channel for send/receive."""
    bus_tx = can.Bus(interface="virtual", channel="test_chan", receive_own_messages=False)
    bus_rx = can.Bus(interface="virtual", channel="test_chan", receive_own_messages=False)
    yield bus_tx, bus_rx
    bus_tx.shutdown()
    bus_rx.shutdown()


@pytest.fixture
def json_db(tmp_path):
    """Create a simple can.json for testing."""
    import json

    db_file = tmp_path / "test.json"
    db_file.write_text(
        json.dumps(
            {
                "messages": {
                    "TestMsg": {
                        "id": 100,
                        "size": 8,
                        "signals": {
                            "Speed": {
                                "start_bit": 0,
                                "length": 16,
                                "factor": 0.01,
                                "offset": 0,
                                "unit": "km/h",
                                "is_signed": False,
                                "byte_order": "little_endian",
                                "minimum": 0,
                                "maximum": 655.35,
                            },
                            "Temp": {
                                "start_bit": 16,
                                "length": 8,
                                "factor": 1.0,
                                "offset": -40,
                                "unit": "degC",
                                "is_signed": False,
                                "byte_order": "little_endian",
                                "minimum": -40,
                                "maximum": 215,
                            },
                        },
                    }
                }
            }
        )
    )
    loader = DatabaseLoader()
    loader.load(str(db_file))
    return loader


# ── bus_factory ───────────────────────────────────────────────────────────────


def test_create_virtual_bus():
    bus = create_virtual_bus("test_vbus")
    assert bus is not None
    bus.shutdown()


def test_create_bus_from_config():
    """create_bus() with virtual interface from CANConfig."""
    cfg = CANConfig(interface="virtual", channel="cfg_test", bitrate=500000)
    bus = create_bus(cfg)
    assert bus is not None
    bus.shutdown()


# ── CANWriter ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_writer_send_signal(virtual_bus_pair, json_db):
    bus_tx, bus_rx = virtual_bus_pair
    writer = CANWriter(bus=bus_tx, db=json_db)
    await writer.send_signal("Speed", 65.0)
    assert writer._sent_count == 1

    # Read message from the other end of the virtual bus
    msg = bus_rx.recv(timeout=1.0)
    assert msg is not None
    assert msg.arbitration_id == 100


@pytest.mark.asyncio
async def test_writer_send_message(virtual_bus_pair, json_db):
    bus_tx, bus_rx = virtual_bus_pair
    writer = CANWriter(bus=bus_tx, db=json_db)
    await writer.send_message(100, {"Speed": 80.0, "Temp": 90.0})
    assert writer._sent_count == 1


@pytest.mark.asyncio
async def test_writer_unknown_signal_raises(virtual_bus_pair, json_db):
    bus_tx, _ = virtual_bus_pair
    writer = CANWriter(bus=bus_tx, db=json_db)
    with pytest.raises(ValueError, match="not found"):
        await writer.send_signal("NoSuchSignal", 1.0)


# ── CANReader ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reader_decodes_frame(virtual_bus_pair, json_db):
    bus_tx, bus_rx = virtual_bus_pair
    queue: asyncio.Queue[DecodedFrame] = asyncio.Queue(maxsize=10)
    reader = CANReader(bus=bus_rx, db=json_db, queue=queue)

    # Start reader in background
    task = asyncio.create_task(reader.start())

    # Send a frame
    data = bytearray(8)
    data[0] = 0x88  # Speed raw = 0x1988 = 6536 → 65.36 km/h
    data[1] = 0x19
    data[2] = 130  # Temp raw = 130 → 130 - 40 = 90°C
    bus_tx.send(can.Message(arbitration_id=100, data=bytes(data)))

    # Wait for decoded frame in queue
    frame = await asyncio.wait_for(queue.get(), timeout=3.0)
    assert isinstance(frame, DecodedFrame)
    assert frame.raw.msg_id == 100
    assert "Speed" in frame.signals
    assert frame.signals["Speed"] == pytest.approx(65.36, abs=0.02)
    assert frame.signals["Temp"] == pytest.approx(90.0)

    reader.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_reader_filter_ids(virtual_bus_pair, json_db):
    """Reader with filter_ids should ignore non-matching frames."""
    bus_tx, bus_rx = virtual_bus_pair
    queue: asyncio.Queue[DecodedFrame] = asyncio.Queue(maxsize=10)
    reader = CANReader(bus=bus_rx, db=json_db, queue=queue, filter_ids={200})

    task = asyncio.create_task(reader.start())

    # Send a frame with msg_id=100 (not in filter)
    bus_tx.send(can.Message(arbitration_id=100, data=bytes(8)))
    # Give reader time to process
    await asyncio.sleep(0.3)
    assert queue.empty()

    reader.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_reader_stop():
    """Reader.stop() should terminate the read loop cleanly."""
    bus = can.Bus(interface="virtual", channel="stop_test")
    loader = DatabaseLoader()
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    reader = CANReader(bus=bus, db=loader, queue=queue)

    task = asyncio.create_task(reader.start())
    await asyncio.sleep(0.2)
    reader.stop()
    # Should finish within a reasonable time (recv timeout = 1s)
    await asyncio.wait_for(task, timeout=3.0)
    bus.shutdown()


def test_create_bus_virtual():
    """Test create_bus with virtual interface returns VirtualBus."""
    cfg = CANConfig(interface="virtual", channel="vcan0", bitrate=500000)
    bus = create_bus(cfg)
    import can.interfaces.virtual

    assert isinstance(bus, can.interfaces.virtual.VirtualBus)
    bus.shutdown()


def test_create_bus_with_kwargs_override():
    """Ensure kwargs correctly override properties from CANConfig."""
    cfg = CANConfig(interface="virtual", channel="vcan0", bitrate=500000)
    # The factory forwards kwargs. receive_own_messages is a valid kwarg for can.Bus
    bus = create_bus(cfg, channel="vcan1", receive_own_messages=True)
    import can.interfaces.virtual

    assert isinstance(bus, can.interfaces.virtual.VirtualBus)
    assert bus.channel_info == "Virtual bus channel vcan1"
    bus.shutdown()


def test_create_bus_invalid_interface():
    """Ensure invalid interfaces raise the expected error."""
    cfg = CANConfig(interface="invalid_iface", channel="can0")
    with pytest.raises(can.CanInterfaceNotImplementedError):
        create_bus(cfg)


@patch("can.Bus")
def test_create_bus_socketcan_parameters(mock_bus):
    """Ensure interface='socketcan' passes bitrate, mocking can.Bus to avoid hardware errors."""
    # We mock can.Bus because creating a socketcan bus requires Linux and actual hardware support
    mock_bus.return_value = "MockSocketcanBus"

    cfg = CANConfig(interface="socketcan", channel="can0", bitrate=250000)
    bus = create_bus(cfg)

    # Asserting the factory returned the mock instance correctly
    assert bus == "MockSocketcanBus"
    mock_bus.assert_called_once_with(interface="socketcan", channel="can0", bitrate=250000)
