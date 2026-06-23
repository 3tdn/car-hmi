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

    # Mock stop so we can verify it's not called
    reader.stop = Mock()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await reader._reconnect()

        # Verify sleep was called once with delay=1
        mock_sleep.assert_called_once_with(1)

        # Verify bus shutdown was called
        bus_mock.shutdown.assert_called_once()

        # Verify factory was called and bus was replaced
        bus_factory_mock.assert_called_once()
        assert reader._bus == bus_factory_mock.return_value

        # Verify stop was not called
        reader.stop.assert_not_called()


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

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await reader._reconnect()

        # Verify sleep was called 3 times with delays 1, 2, 4
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

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
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("src.can_io.reader.logger") as mock_logger,
    ):
        await reader._reconnect()

        # Verify sleep was called once with delay=1
        mock_sleep.assert_called_once_with(1)

        # Verify bus shutdown was called
        bus_mock.shutdown.assert_called_once()

        # Verify logger.warning was called
        mock_logger.warning.assert_called_with(
            "No bus_factory \u2014 cannot re-open bus; stopping reader"
        )

        # Verify stop was called
        reader.stop.assert_called_once()


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
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("src.can_io.reader.logger") as mock_logger,
    ):
        await reader._reconnect()

        # Verify sleep was called 3 times with delays 1, 2, 4
        assert mock_sleep.call_count == 3
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

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
