"""Unit tests for CANReader and CANWriter."""

from __future__ import annotations

import pytest


def test_raw_can_frame_fields():
    from src.can_io.reader import RawCANFrame

    f = RawCANFrame(
        timestamp=1.0, bus="vcan0", msg_id=0x18F, is_extended=False, is_fd=False, data=b"\x00\x01"
    )
    assert f.msg_id == 0x18F
    assert f.data == b"\x00\x01"
    assert f.bus == "vcan0"


def test_decoded_frame_fields():
    from src.can_io.reader import DecodedFrame, RawCANFrame

    raw = RawCANFrame(
        timestamp=2.0, bus="vcan0", msg_id=0x18F, is_extended=False, is_fd=False, data=b"\x00"
    )
    df = DecodedFrame(
        raw=raw,
        signals={"EngineRPM": 1500.0},
        msg_name="EngineData",
    )
    assert df.signals["EngineRPM"] == pytest.approx(1500.0)
    assert df.msg_name == "EngineData"


@pytest.mark.asyncio
async def test_enqueue_refreshes_stale_priority_signal():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader, DecodedFrame, RawCANFrame

    bus_mock = Mock(spec=can.BusABC)
    db_mock = Mock()
    db_mock.messages = {100: SimpleNamespace(signals={"Speed": None})}
    queue_mock = asyncio.Queue(maxsize=10)

    reader = CANReader(bus=bus_mock, db=db_mock, queue=queue_mock, priority_sec=1.0)
    reader._decode = Mock(
        return_value=DecodedFrame(
            raw=RawCANFrame(
                timestamp=1.0,
                bus="test",
                msg_id=100,
                is_extended=False,
                is_fd=False,
                data=b"\x01",
            ),
            signals={"Speed": 10.0},
            msg_name="TestMsg",
        )
    )

    msg = can.Message(arbitration_id=100, data=b"\x01")

    reader._enqueue_sync(msg, arrival=1.0)
    assert queue_mock.qsize() == 1

    reader._enqueue_sync(msg, arrival=1.5)
    assert queue_mock.qsize() == 1

    first = queue_mock.get_nowait()
    assert first.signals == {"Speed": 10.0}

    reader._enqueue_sync(msg, arrival=2.2)
    assert queue_mock.qsize() == 1
    second = queue_mock.get_nowait()
    assert second.signals == {"Speed": 10.0}


def test_recv_loop_deduplicates_before_event_loop_callback():
    """Repeated raw frames should not create event-loop callback backlog."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    class FakeBus:
        INTERFACE = "virtual"
        channel = "vcan0"

        def __init__(self, responses):
            self._responses = list(responses)

        def recv(self, timeout=0.2):
            item = self._responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

    class FakeLoop:
        def __init__(self):
            self.calls = []

        def call_soon_threadsafe(self, callback, *args):
            self.calls.append((callback, args))

    msg = can.Message(arbitration_id=100, data=b"\x01", timestamp=1.0)
    bus = FakeBus([msg, msg, msg, can.CanError("stop")])
    db_mock = Mock()
    db_mock.messages = {100: SimpleNamespace(name="TestMsg", signals={"Speed": None})}
    db_mock.decode_frame.return_value = {"Speed": 10.0}
    queue_mock = asyncio.Queue(maxsize=10)
    reader = CANReader(bus=bus, db=db_mock, queue=queue_mock)
    loop = FakeLoop()

    reader._running = True
    reader._recv_loop(loop)

    assert len(loop.calls) == 1
    assert loop.calls[0][0] == reader._drain_pending_frames
    assert db_mock.decode_frame.call_count == 1
    loop.calls[0][0](*loop.calls[0][1])
    assert queue_mock.get_nowait().signals == {"Speed": 10.0}


def test_reader_allowlist_rejects_unknown_id_before_decode_and_cache():
    """Frames outside the DBC allowlist must not consume decode or cache state."""
    import asyncio
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    db_mock = Mock()
    db_mock.messages = {100: Mock()}
    reader = CANReader(
        bus=Mock(spec=can.BusABC),
        db=db_mock,
        queue=asyncio.Queue(),
        filter_ids=set(db_mock.messages),
        max_rate_hz=5000.0,
    )

    frame = reader._prepare_frame(
        can.Message(arbitration_id=200, data=b"\x01"),
        arrival=1.0,
    )

    assert frame is None
    db_mock.decode_frame.assert_not_called()
    assert reader._last_enqueue == {}
    assert reader._last_msg_data == {}


def test_recv_loop_coalesces_continuously_changing_frames_before_callback():
    """Changing payloads must not bypass the bounded callback ingress."""
    import asyncio
    from types import SimpleNamespace

    import can

    from src.can_io.reader import CANReader

    class FakeBus:
        INTERFACE = "virtual"
        channel = "vcan0"

        def __init__(self, count: int):
            self._count = count
            self._index = 0

        def recv(self, timeout=0.2):
            if self._index >= self._count:
                raise can.CanError("stop")
            value = self._index % 256
            self._index += 1
            return can.Message(arbitration_id=100, data=bytes([value]), timestamp=1.0)

    class FakeDB:
        def __init__(self):
            self.messages = {100: SimpleNamespace(name="TestMsg", signals={"Speed": None})}

        def decode_frame(self, _msg_id, data):
            return {"Speed": float(data[0])}

    class FakeLoop:
        def __init__(self):
            self.calls = []

        def call_soon_threadsafe(self, callback, *args):
            self.calls.append((callback, args))

        def call_soon(self, callback, *args):
            self.calls.append((callback, args))

    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    reader = CANReader(bus=FakeBus(3_000), db=FakeDB(), queue=queue, max_rate_hz=0)
    loop = FakeLoop()

    reader._running = True
    reader._recv_loop(loop)

    assert len(loop.calls) == 1
    metrics = reader.get_metrics()
    assert metrics["received_frames"] == 3_000
    assert metrics["coalesced_frames"] == 2_999
    assert metrics["pending_frames"] == 1

    loop.calls.pop(0)[0]()
    assert queue.qsize() == 1
    assert queue.get_nowait().signals == {"Speed": 183.0}


@pytest.mark.asyncio
async def test_reconnect_success_first_attempt():
    import asyncio
    from unittest.mock import AsyncMock, Mock, patch

    import can

    from src.can_io.reader import CANReader

    bus_mock = Mock(spec=can.BusABC)
    db_mock = Mock()
    queue_mock = asyncio.Queue()
    bus_factory_mock = Mock(return_value=Mock(spec=can.BusABC))

    reader = CANReader(
        bus=bus_mock,
        db=db_mock,
        queue=queue_mock,
        bus_factory=bus_factory_mock,
        max_reconnect_retries=3,
    )
    reader._running = True
    reader._last_enqueue[100] = 1.0
    reader._last_msg_data[100] = b"old"
    reader._last_signal_values["Speed"] = 10.0
    reader._signal_last_enqueue_time["Speed"] = 1.0

    # Mock stop so we can verify it's not called
    reader.stop = Mock()

    with patch.object(
        reader, "_wait_for_reconnect_delay", new_callable=AsyncMock
    ) as mock_wait:
        await reader._reconnect()

        # Verify the staged delay was requested once with delay=1
        mock_wait.assert_awaited_once_with(1)

        # Verify bus shutdown was called
        bus_mock.shutdown.assert_called_once()

        # Verify factory was called and bus was replaced
        bus_factory_mock.assert_called_once()
        assert reader._bus == bus_factory_mock.return_value
        assert reader._last_enqueue == {}
        assert reader._last_msg_data == {}
        assert reader._last_signal_values == {}
        assert reader._signal_last_enqueue_time == {}

        # Verify stop was not called
        reader.stop.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_can_start_without_an_initial_bus():
    import asyncio
    from unittest.mock import AsyncMock, Mock, patch

    import can

    from src.can_io.reader import CANReader

    replacement_bus = Mock(spec=can.BusABC)
    bus_factory = Mock(return_value=replacement_bus)
    reader = CANReader(
        bus=None,
        db=Mock(),
        queue=asyncio.Queue(),
        bus_factory=bus_factory,
    )
    reader._running = True

    with patch.object(
        reader, "_wait_for_reconnect_delay", new_callable=AsyncMock
    ) as mock_wait:
        await reader._reconnect(max_retries=1)

    mock_wait.assert_awaited_once_with(1)
    bus_factory.assert_called_once_with()
    assert reader._bus is replacement_bus


@pytest.mark.asyncio
async def test_frontend_activity_wakes_reconnect_wait_and_is_throttled():
    import asyncio
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    reader = CANReader(
        bus=Mock(spec=can.BusABC),
        db=Mock(),
        queue=asyncio.Queue(),
        frontend_retry_enabled=True,
    )
    reader._running = True
    reader._reconnecting = True

    wait_task = asyncio.create_task(reader._wait_for_reconnect_delay(3600))
    await asyncio.sleep(0)

    assert reader.notify_frontend_activity() is True
    await asyncio.wait_for(wait_task, timeout=0.1)
    assert reader.notify_frontend_activity() is False


def test_frontend_activity_does_not_wake_fixed_channel_reader():
    import asyncio
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    reader = CANReader(
        bus=Mock(spec=can.BusABC),
        db=Mock(),
        queue=asyncio.Queue(),
    )
    reader._running = True
    reader._reconnecting = True

    assert reader.notify_frontend_activity() is False
    assert reader._reconnect_wakeup.is_set() is False


@pytest.mark.asyncio
async def test_silent_reopened_bus_keeps_advancing_reconnect_backoff():
    import asyncio
    from unittest.mock import AsyncMock, Mock, patch

    import can

    from src.can_io.reader import CANReader

    reader = CANReader(
        bus=Mock(spec=can.BusABC),
        db=Mock(),
        queue=asyncio.Queue(),
        bus_factory=Mock(return_value=Mock(spec=can.BusABC)),
        max_reconnect_retries=5,
    )
    reader._running = True
    reader._reconnect_attempt = 5

    with patch.object(
        reader, "_wait_for_reconnect_delay", new_callable=AsyncMock
    ) as mock_wait:
        await reader._reconnect(max_retries=1)

    mock_wait.assert_awaited_once_with(30)
    assert reader._reconnect_attempt == 6
    assert reader._last_recv_monotonic == 0.0
    assert reader._bus_opened_monotonic > 0.0


@pytest.mark.asyncio
async def test_reconnect_exponential_backoff():
    import asyncio
    from unittest.mock import AsyncMock, Mock, patch

    import can

    from src.can_io.reader import CANReader

    bus_mock = Mock(spec=can.BusABC)
    db_mock = Mock()
    queue_mock = asyncio.Queue()

    # Factory raises CanError first two times, then succeeds
    successful_bus_mock = Mock(spec=can.BusABC)
    bus_factory_mock = Mock(
        side_effect=[can.CanError("Fail 1"), can.CanError("Fail 2"), successful_bus_mock]
    )

    reader = CANReader(
        bus=bus_mock,
        db=db_mock,
        queue=queue_mock,
        bus_factory=bus_factory_mock,
        max_reconnect_retries=5,
    )
    reader._running = True

    reader.stop = Mock()

    with patch.object(
        reader, "_wait_for_reconnect_delay", new_callable=AsyncMock
    ) as mock_wait:
        await reader._reconnect(max_retries=3)

        # Verify the staged waits were 1, 2, 4 seconds
        assert mock_wait.await_count == 3
        mock_wait.assert_any_await(1)
        mock_wait.assert_any_await(2)
        mock_wait.assert_any_await(4)

        # Verify bus shutdown was called 3 times (on the original bus)
        # Note: `self._bus.shutdown()` is called, and `self._bus` is replaced if `_bus_factory()` succeeds.
        # If it raises `CanError`, `self._bus` remains `bus_mock`. So it's called 3 times on `bus_mock`.
        assert bus_mock.shutdown.call_count == 3

        # Verify factory was called 3 times
        assert bus_factory_mock.call_count == 3

        # Verify bus was updated to the successful one
        assert reader._bus == successful_bus_mock

        # Verify stop was not called
        reader.stop.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_no_bus_factory():
    import asyncio
    from unittest.mock import AsyncMock, Mock, patch

    import can

    from src.can_io.reader import CANReader

    bus_mock = Mock(spec=can.BusABC)
    db_mock = Mock()
    queue_mock = asyncio.Queue()

    reader = CANReader(
        bus=bus_mock,
        db=db_mock,
        queue=queue_mock,
        bus_factory=None,
        max_reconnect_retries=3,
    )
    reader._running = True

    reader.stop = Mock()

    with (
        patch.object(
            reader, "_wait_for_reconnect_delay", new_callable=AsyncMock
        ) as mock_wait,
        patch("src.can_io.reader.logger") as mock_logger,
    ):
        await reader._reconnect()

        # Verify the staged delay was requested once with delay=1
        mock_wait.assert_awaited_once_with(1)

        # Verify bus shutdown was called
        bus_mock.shutdown.assert_called_once()

        # Verify logger.warning was called
        mock_logger.warning.assert_called_with(
            "No bus_factory \u2014 cannot re-open bus; stopping reader"
        )

        # Verify stop was called
        reader.stop.assert_called_once()


def test_reconnect_delay_uses_longer_retry_cycles():
    from src.can_io.reader import CANReader

    initial_retries = 5
    assert CANReader._reconnect_delay(1, initial_retries) == 1
    assert CANReader._reconnect_delay(5, initial_retries) == 16
    assert CANReader._reconnect_delay(6, initial_retries) == 30
    assert CANReader._reconnect_delay(15, initial_retries) == 30
    assert CANReader._reconnect_delay(16, initial_retries) == 60
    assert CANReader._reconnect_delay(26, initial_retries) == 120
    assert CANReader._reconnect_delay(100, initial_retries) == 3600


def test_reader_detects_silent_bus_after_stale_threshold():
    import time
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    reader = CANReader(
        bus=Mock(spec=can.BusABC),
        db=Mock(),
        queue=Mock(),
        stale_threshold_sec=1.0,
    )
    reader._last_recv_monotonic = time.monotonic() - 1.1

    assert reader._is_bus_stale() is True


def test_reader_detects_bus_that_is_silent_from_startup():
    import time
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    reader = CANReader(
        bus=Mock(spec=can.BusABC),
        db=Mock(),
        queue=Mock(),
        stale_threshold_sec=1.0,
    )
    reader._bus_opened_monotonic = time.monotonic() - 1.1

    assert reader._last_recv_monotonic == 0.0
    assert reader._is_bus_stale() is True


def test_unrelated_can_id_confirms_fixed_channel_transport_health():
    import asyncio
    from unittest.mock import Mock

    import can

    from src.can_io.reader import CANReader

    class BusWithUnrelatedFrame:
        def __init__(self):
            self.reader = None
            self.calls = 0

        def recv(self, timeout):
            self.calls += 1
            if self.calls == 1:
                return can.Message(arbitration_id=0x456, data=[0])
            self.reader._running = False
            return None

    bus = BusWithUnrelatedFrame()
    reader = CANReader(
        bus=bus,
        db=Mock(),
        queue=asyncio.Queue(),
        filter_ids={0x123},
    )
    bus.reader = reader
    reader._running = True
    reader._reconnect_attempt = 4

    reader._recv_loop(Mock())

    assert reader._received_count == 1
    assert reader._last_recv_monotonic > 0.0
    assert reader._last_frame_timestamp > 0.0
    assert reader._reconnect_attempt == 0


@pytest.mark.asyncio
async def test_reconnect_exhaust_retries():
    import asyncio
    from unittest.mock import AsyncMock, Mock, patch

    import can

    from src.can_io.reader import CANReader

    bus_mock = Mock(spec=can.BusABC)
    db_mock = Mock()
    queue_mock = asyncio.Queue()

    # Factory raises CanError always
    bus_factory_mock = Mock(side_effect=can.CanError("Persistent Fail"))

    reader = CANReader(
        bus=bus_mock,
        db=db_mock,
        queue=queue_mock,
        bus_factory=bus_factory_mock,
        max_reconnect_retries=3,
    )
    reader._running = True

    reader.stop = Mock()

    with (
        patch.object(
            reader, "_wait_for_reconnect_delay", new_callable=AsyncMock
        ) as mock_wait,
        patch("src.can_io.reader.logger") as mock_logger,
    ):
        await reader._reconnect(max_retries=3)

        # Verify the staged waits were 1, 2, 4 seconds
        assert mock_wait.await_count == 3
        mock_wait.assert_any_await(1)
        mock_wait.assert_any_await(2)
        mock_wait.assert_any_await(4)

        # Verify bus shutdown was called 3 times
        assert bus_mock.shutdown.call_count == 3

        # Verify factory was called 3 times
        assert bus_factory_mock.call_count == 3

        # Verify logger.critical was called
        mock_logger.critical.assert_called_with(
            "CAN bus reconnect failed after %d attempts \u2014 supervisor must intervene",
            3,
        )

        # Verify stop was called
        reader.stop.assert_called_once()
