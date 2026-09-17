"""Tests for bus_factory, CANReader, CANWriter with virtual bus."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import call, patch

import can
import pytest

from src.can_io.bus_factory import (
    create_bus,
    create_virtual_bus,
    list_up_socketcan_channels,
    resolve_auto_match_ids,
)
from src.can_io.parser import DatabaseLoader
from src.can_io.reader import CANReader, DecodedFrame
from src.can_io.writer import CANWriter, CANWriteRejectedError, CANWriterRouter
from src.core.config import CANConfig, WriterConfig
from src.core.signal_store import SignalStore


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
async def test_writer_preserves_unwritten_signals_by_default(virtual_bus_pair, json_db):
    """Single-signal writes preserve the latest values of sibling signals by default."""
    bus_tx, bus_rx = virtual_bus_pair
    store = SignalStore()
    await store.update("Temp", 90.0)
    writer = CANWriter(bus=bus_tx, db=json_db, signal_store=store)

    await writer.send_signal("Speed", 65.0)

    msg = bus_rx.recv(timeout=1.0)
    assert msg is not None
    decoded = json_db.decode_frame(msg.arbitration_id, bytes(msg.data))
    assert decoded["Speed"] == pytest.approx(65.0)
    assert decoded["Temp"] == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_writer_zeroes_unwritten_signals_for_batch_write(virtual_bus_pair, json_db):
    """The zero policy takes precedence over cached SignalStore values for batch writes."""
    bus_tx, bus_rx = virtual_bus_pair
    store = SignalStore()
    await store.update("Temp", 90.0)
    writer = CANWriter(
        bus=bus_tx,
        db=json_db,
        signal_store=store,
        writer_config=WriterConfig(use_prevalue_for_unwritten_signal=False),
    )

    await writer.send_signals_batch({"Speed": 65.0})

    msg = bus_rx.recv(timeout=1.0)
    assert msg is not None
    decoded = json_db.decode_frame(msg.arbitration_id, bytes(msg.data))
    assert decoded["Speed"] == pytest.approx(65.0)
    assert decoded["Temp"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_writer_runtime_config_preserves_replacement_periodic_task(
    virtual_bus_pair, json_db
):
    """A cancelled sender must not remove a newer task for the same message."""
    bus_tx, _ = virtual_bus_pair
    writer = CANWriter(
        bus=bus_tx,
        db=json_db,
        writer_config=WriterConfig(
            periodic_mode=True,
            periodic_time_step=1000,
            periodic_duration=10000,
        ),
    )
    old_task = asyncio.create_task(
        writer._periodic_sender(100, json_db.messages[100], {"Speed": 1.0})
    )
    writer._periodic_tasks[100] = old_task
    await asyncio.sleep(0)

    writer.apply_runtime_config(
        WriterConfig(
            periodic_mode=False,
            periodic_time_step=25,
            periodic_duration=50,
            use_prevalue_for_unwritten_signal=False,
        )
    )
    replacement = asyncio.create_task(asyncio.sleep(10))
    writer._periodic_tasks[100] = replacement
    await old_task

    assert writer._periodic_tasks[100] is replacement
    assert writer._periodic_mode is False
    assert writer._periodic_time_step_ms == 25
    assert writer._periodic_duration_ms == 50
    assert writer._use_prevalue_for_unwritten_signal is False

    replacement.cancel()
    with pytest.raises(asyncio.CancelledError):
        await replacement


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


@pytest.mark.asyncio
async def test_v8_oms_state_is_rx_only_and_sensor_fusion_request_is_tx(
    virtual_bus_pair,
):
    bus_tx, _ = virtual_bus_pair
    loader = DatabaseLoader()
    dbc_path = (
        Path(__file__).resolve().parents[2]
        / "db/can_db/Interface_Panther_To_CarPC_v8.dbc"
    )
    loader.load_dbc(dbc_path)
    writer = CANWriter(bus=bus_tx, db=loader)
    router = CANWriterRouter()
    router.register(loader, writer)

    with pytest.raises(CANWriteRejectedError) as exc_info:
        await router.send_signal("OMS_State_Camera", 1.0)

    message = str(exc_info.value)
    assert "message 'MON_OMS_State'" in message
    assert "msg_id=0xb8" in message
    assert "DBC sender(s): [SIMI]" in message
    assert writer.validate_signal_tx("HMI_SensorFusion_Camera").msg_id == 0x84


@pytest.fixture
def v8_db():
    loader = DatabaseLoader()
    dbc_path = (
        Path(__file__).resolve().parents[2]
        / "db/can_db/Interface_Panther_To_CarPC_v8.dbc"
    )
    loader.load_dbc(dbc_path)
    return loader


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve_unwritten", [False, True])
async def test_elk_batch_uses_locking_statuses_for_unwritten_requests(
    virtual_bus_pair,
    v8_db,
    preserve_unwritten,
):
    bus_tx, bus_rx = virtual_bus_pair
    store = SignalStore()
    await store.bulk_update(
        {
            "ELK_FL_LockingStatus": 1.0,
            "ELK_FR_LockingStatus": 0.0,
            "ELK_RL1_LockingStatus": 1.0,
            "ELK_RL2_LockingStatus": 0.0,
            "ELK_RR1_LockingStatus": 1.0,
            "ELK_FL_LockingRequest": 0.0,
            "ELK_RL1_LockingRequest": 0.0,
            "ELK_RR1_LockingRequest": 0.0,
        }
    )
    writer = CANWriter(
        bus=bus_tx,
        db=v8_db,
        signal_store=store,
        writer_config=WriterConfig(
            use_prevalue_for_unwritten_signal=preserve_unwritten
        ),
    )

    await writer.send_signals_batch({"ELK_FR_LockingRequest": 1.0})

    msg = bus_rx.recv(timeout=1.0)
    assert msg is not None
    assert msg.arbitration_id == 0x92
    decoded = v8_db.decode_frame(msg.arbitration_id, bytes(msg.data))
    assert decoded == {
        "ELK_ResetErrorFlags": 0.0,
        "ELK_FL_LockingRequest": 1.0,
        "ELK_FR_LockingRequest": 1.0,
        "ELK_RR1_LockingRequest": 1.0,
        "ELK_RL2_LockingRequest": 0.0,
        "ELK_RL1_LockingRequest": 1.0,
    }


@pytest.mark.asyncio
async def test_elk_batch_groups_two_explicit_requests_into_one_frame(
    virtual_bus_pair,
    v8_db,
):
    bus_tx, bus_rx = virtual_bus_pair
    store = SignalStore()
    await store.bulk_update(
        {
            "ELK_FL_LockingStatus": 0.0,
            "ELK_FR_LockingStatus": 1.0,
            "ELK_RL1_LockingStatus": 0.0,
            "ELK_RL2_LockingStatus": 1.0,
            "ELK_RR1_LockingStatus": 1.0,
        }
    )
    writer = CANWriter(bus=bus_tx, db=v8_db, signal_store=store)

    sent = await writer.send_signals_batch(
        {
            "ELK_FL_LockingRequest": 1.0,
            "ELK_RR1_LockingRequest": 0.0,
        }
    )
    assert sent == {
        "ELK_FL_LockingRequest": 1.0,
        "ELK_RR1_LockingRequest": 0.0,
    }

    msg = bus_rx.recv(timeout=1.0)
    assert msg is not None
    assert msg.arbitration_id == 0x92
    decoded = v8_db.decode_frame(msg.arbitration_id, bytes(msg.data))
    assert decoded == {
        "ELK_ResetErrorFlags": 0.0,
        "ELK_FL_LockingRequest": 1.0,
        "ELK_FR_LockingRequest": 1.0,
        "ELK_RR1_LockingRequest": 0.0,
        "ELK_RL2_LockingRequest": 1.0,
        "ELK_RL1_LockingRequest": 0.0,
    }
    assert bus_rx.recv(timeout=0.05) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("preserve_unwritten", [False, True])
async def test_sensor_fusion_batch_uses_oms_state_for_unwritten_requests(
    virtual_bus_pair,
    v8_db,
    preserve_unwritten,
):
    bus_tx, bus_rx = virtual_bus_pair
    store = SignalStore()
    await store.bulk_update(
        {
            "OMS_State_CapSensor": 1.0,
            "OMS_State_StrainGauge": 0.0,
            "OMS_State_Camera": 1.0,
            "HMI_SensorFusion_CapSensor": 0.0,
            "HMI_SensorFusion_Camera": 0.0,
        }
    )
    writer = CANWriter(
        bus=bus_tx,
        db=v8_db,
        signal_store=store,
        writer_config=WriterConfig(
            use_prevalue_for_unwritten_signal=preserve_unwritten
        ),
    )

    await writer.send_signals_batch({"HMI_SensorFusion_StrainGage": 1.0})

    msg = bus_rx.recv(timeout=1.0)
    assert msg is not None
    assert msg.arbitration_id == 0x84
    decoded = v8_db.decode_frame(msg.arbitration_id, bytes(msg.data))
    assert decoded == {
        "HMI_SensorFusion_CapSensor": 1.0,
        "HMI_SensorFusion_StrainGage": 1.0,
        "HMI_SensorFusion_Camera": 1.0,
    }


# ── CANReader ─────────────────────────────────────────────────────────────────


def test_reader_applies_runtime_config(virtual_bus_pair, json_db):
    _, bus_rx = virtual_bus_pair
    reader = CANReader(bus=bus_rx, db=json_db, queue=asyncio.Queue(maxsize=10))

    reader.apply_runtime_config(
        queue_policy="drop_oldest",
        max_rate_hz=25.0,
        priority_sec=2.5,
        stale_threshold_sec=7.0,
    )

    assert reader._policy == "drop_oldest"
    assert reader._min_interval == pytest.approx(0.04)
    assert reader._priority_sec == pytest.approx(2.5)
    assert reader._stale_threshold_sec == pytest.approx(7.0)


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


def test_list_up_socketcan_channels_filters_type_and_flags_and_sorts_naturally(tmp_path):
    def add_interface(name: str, hardware_type: str, flags: str) -> None:
        interface = tmp_path / name
        interface.mkdir()
        (interface / "type").write_text(hardware_type)
        (interface / "flags").write_text(flags)

    add_interface("can10", "280", "0x1")
    add_interface("can2", "280", "0x1001")
    add_interface("can0", "280", "0x0")
    add_interface("eth0", "1", "0x1")

    assert list_up_socketcan_channels(tmp_path) == ["can2", "can10"]


def test_auto_tracking_resolves_only_configured_messages():
    db = DatabaseLoader()
    db.load_dbc("db/can_db/Interface_Panther_To_CarPC_v9.dbc")
    cfg = CANConfig(channel_tracking_signals=["COM_Status_ElkCan"])
    expected = db.get_message_for_signal("COM_Status_ElkCan").msg_id

    assert resolve_auto_match_ids(cfg, db) == {expected}
    assert len(resolve_auto_match_ids(CANConfig(), db)) > 1
    cfg.channel_tracking_signals.append("missing_signal")
    with pytest.raises(ValueError, match="missing_signal"):
        resolve_auto_match_ids(cfg, db)


@patch("src.can_io.bus_factory.list_up_socketcan_channels", return_value=["can0", "can2"])
@patch("src.can_io.bus_factory.can.Bus")
def test_create_bus_auto_selects_up_channel_with_dbc_traffic(mock_bus, _mock_channels):
    class ProbeBus:
        def __init__(self, messages):
            self.messages = iter(messages)
            self.shutdown_called = False

        def recv(self, timeout):
            return next(self.messages, None)

        def shutdown(self):
            self.shutdown_called = True

        def set_filters(self, filters):
            self.filters = filters

    silent_bus = ProbeBus([can.Message(arbitration_id=0x456, data=[0])])
    matching_bus = ProbeBus([can.Message(arbitration_id=0x123, data=[0])])
    mock_bus.side_effect = [silent_bus, matching_bus]
    cfg = CANConfig(interface="socketcan", channel="auto", bitrate=500000)

    selected = create_bus(cfg, auto_match_ids={0x123}, auto_probe_timeout_sec=0.1)

    assert selected is matching_bus
    assert selected._car_hmi_prefetched_message.arbitration_id == 0x123
    assert silent_bus.shutdown_called is True
    assert matching_bus.shutdown_called is False
    assert matching_bus.filters is None
    expected_filters = [{"can_id": 0x123, "can_mask": 0x1FFFFFFF, "extended": False}]
    assert mock_bus.call_args_list == [
        call(
            interface="socketcan",
            channel="can0",
            bitrate=500000,
            can_filters=expected_filters,
        ),
        call(
            interface="socketcan",
            channel="can2",
            bitrate=500000,
            can_filters=expected_filters,
        ),
    ]


@patch("src.can_io.bus_factory.list_up_socketcan_channels", return_value=[])
def test_create_bus_auto_requires_an_up_socketcan_channel(_mock_channels):
    cfg = CANConfig(interface="socketcan", channel="auto")

    with pytest.raises(can.CanInitializationError, match="No UP SocketCAN interface"):
        create_bus(cfg, auto_match_ids={0x123}, auto_probe_timeout_sec=0)


@patch("src.can_io.bus_factory.list_up_socketcan_channels", return_value=["can0", "can2"])
@patch("src.can_io.bus_factory.can.Bus")
def test_create_bus_auto_closes_all_candidates_when_no_dbc_traffic(mock_bus, _mock_channels):
    class SilentBus:
        def __init__(self):
            self.shutdown_called = False

        def recv(self, timeout):
            return None

        def shutdown(self):
            self.shutdown_called = True

    candidates = [SilentBus(), SilentBus()]
    mock_bus.side_effect = candidates
    cfg = CANConfig(interface="socketcan", channel="auto")

    with pytest.raises(can.CanInitializationError, match="No UP SocketCAN interface received"):
        create_bus(cfg, auto_match_ids={0x123}, auto_probe_timeout_sec=0)

    assert all(bus.shutdown_called for bus in candidates)


@patch("src.can_io.bus_factory.list_up_socketcan_channels", return_value=["can0"])
@patch("src.can_io.bus_factory.can.Bus")
def test_create_bus_auto_drops_broken_probe_without_busy_loop(mock_bus, _mock_channels):
    class BrokenBus:
        def __init__(self):
            self.recv_calls = 0
            self.shutdown_called = False

        def recv(self, timeout):
            self.recv_calls += 1
            raise can.CanError("link down")

        def shutdown(self):
            self.shutdown_called = True

    broken_bus = BrokenBus()
    mock_bus.return_value = broken_bus
    cfg = CANConfig(interface="socketcan", channel="auto")

    with pytest.raises(can.CanInitializationError, match="failed while probing"):
        create_bus(cfg, auto_match_ids={0x123}, auto_probe_timeout_sec=3.0)

    assert broken_bus.recv_calls == 1
    assert broken_bus.shutdown_called is True
