"""Unit tests for signal processing pipeline, filters, and alarms."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_smoothing_filter_ema():
    from src.processor.filters import SmoothingFilter

    f = SmoothingFilter(window=5, method="ema")

    result1 = await f.process({"speed": 10.0})
    assert result1["speed"] == 10.0

    result2 = await f.process({"speed": 20.0})
    # window=5, alpha = 2/(5+1) = 1/3 (constant).
    # ema = 1/3 * 20 + 2/3 * 10 = 13.333
    assert result2["speed"] == pytest.approx(13.333333333333334)

    result3 = await f.process({"speed": 30.0})
    # ema = 1/3 * 30 + 2/3 * 13.333 = 10 + 8.889 = 18.889
    assert result3["speed"] == pytest.approx(18.88888888888889)

    result4 = await f.process({"speed": 40.0})
    # ema = 1/3 * 40 + 2/3 * 18.889 = 13.333 + 12.593 = 25.926
    assert result4["speed"] == pytest.approx(25.925925925925927)

    result5 = await f.process({"speed": 50.0})
    # ema = 1/3 * 50 + 2/3 * 25.926 = 16.667 + 17.284 = 33.951
    assert result5["speed"] == pytest.approx(33.95061728395062)

    result6 = await f.process({"speed": 60.0})
    # alpha = 1/3 (constant). ema = 1/3 * 60 + 2/3 * 33.951 = 20 + 22.634 = 42.634
    assert result6["speed"] == pytest.approx(42.63374485596708)


@pytest.mark.asyncio
async def test_smoothing_filter_moving_avg():
    from src.processor.filters import SmoothingFilter

    f = SmoothingFilter(window=3, method="moving_avg")
    result = await f.process({"speed": 10.0})
    result = await f.process({"speed": 20.0})
    result = await f.process({"speed": 30.0})
    # average of [10, 20, 30]
    assert result["speed"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_rate_limiter_drops_fast_updates():
    from src.processor.filters import RateLimiter

    lim = RateLimiter(max_hz=1.0)  # 1 Hz → min 1 s interval
    r1 = await lim.process({"rpm": 1000.0})
    r2 = await lim.process({"rpm": 2000.0})  # arrives instantly → should be dropped
    assert "rpm" in r1
    assert "rpm" not in r2


@pytest.mark.asyncio
async def test_alarm_checker_critical_high():
    from src.processor.alarms import AlarmChecker, AlarmConfig

    alarms_fired: list = []
    checker = AlarmChecker([AlarmConfig(signal="temp", critical_high=100.0)])
    checker.add_alarm_handler(lambda a: alarms_fired.append(a))

    async def async_handler(a):
        alarms_fired.append(a)

    checker._alarm_handlers = [async_handler]
    await checker.process({"temp": 105.0})
    assert len(alarms_fired) == 1
    assert alarms_fired[0].level == "critical"


@pytest.mark.asyncio
async def test_computed_signals_formula():
    from src.processor.computed import ComputedSignals

    cs = ComputedSignals({"double_rpm": lambda s: s.get("rpm", 0) * 2})
    result = await cs.process({"rpm": 3000.0})
    assert result["double_rpm"] == pytest.approx(6000.0)
    assert result["rpm"] == pytest.approx(3000.0)  # original preserved


@pytest.mark.asyncio
async def test_computed_signals_exception_safety():
    """A formula that raises should not crash the stage — bad key is skipped."""
    from src.processor.computed import ComputedSignals

    def bad_formula(s):
        raise ValueError("formula error")

    cs = ComputedSignals({"bad": bad_formula, "ok": lambda s: 1.0})
    result = await cs.process({"rpm": 100.0})
    # bad key skipped, ok key and original signal still present
    assert "bad" not in result
    assert result.get("ok") == pytest.approx(1.0)
    assert result["rpm"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_alarm_checker_warning_high():
    from src.processor.alarms import AlarmChecker, AlarmConfig

    fired: list = []

    async def handler(a):
        fired.append(a)

    checker = AlarmChecker([AlarmConfig(signal="temp", warning_high=80.0, critical_high=100.0)])
    checker.add_alarm_handler(handler)
    await checker.process({"temp": 85.0})  # above warning, below critical
    assert len(fired) == 1
    assert fired[0].level == "warning"
    assert fired[0].signal == "temp"


@pytest.mark.asyncio
async def test_alarm_checker_warning_low():
    from src.processor.alarms import AlarmChecker, AlarmConfig

    fired: list = []

    async def handler(a):
        fired.append(a)

    checker = AlarmChecker([AlarmConfig(signal="temp", warning_low=20.0, critical_low=5.0)])
    checker.add_alarm_handler(handler)
    await checker.process({"temp": 12.0})  # between critical_low and warning_low
    assert len(fired) == 1
    assert fired[0].level == "warning"


@pytest.mark.asyncio
async def test_alarm_checker_below_critical_low():
    from src.processor.alarms import AlarmChecker, AlarmConfig

    fired: list = []

    async def handler(a):
        fired.append(a)

    checker = AlarmChecker([AlarmConfig(signal="temp", critical_low=5.0)])
    checker.add_alarm_handler(handler)
    await checker.process({"temp": 2.0})
    assert len(fired) == 1
    assert fired[0].level == "critical"


@pytest.mark.asyncio
async def test_alarm_checker_no_alarm_in_range():
    from src.processor.alarms import AlarmChecker, AlarmConfig

    fired: list = []

    async def handler(a):
        fired.append(a)

    checker = AlarmChecker([AlarmConfig(signal="temp", warning_low=20.0, warning_high=80.0)])
    checker.add_alarm_handler(handler)
    await checker.process({"temp": 50.0})  # within normal range
    assert len(fired) == 0


@pytest.mark.asyncio
async def test_rate_limiter_allows_after_interval():
    """RateLimiter should allow a signal through once sufficient time has passed."""
    import asyncio
    from src.processor.filters import RateLimiter

    lim = RateLimiter(max_hz=100.0)  # 100 Hz → min 10 ms interval
    r1 = await lim.process({"x": 1.0})
    assert "x" in r1
    await asyncio.sleep(0.015)  # wait > 10 ms
    r2 = await lim.process({"x": 2.0})
    assert "x" in r2


@pytest.mark.asyncio
async def test_pipeline_unit_stored_in_db(tmp_path):
    """Units seeded into SignalStore should be persisted to the DB via pipeline."""
    import asyncio
    import time

    from src.can_io.reader import DecodedFrame, RawCANFrame
    from src.core.signal_store import SignalStore
    from src.processor.pipeline import SignalPipeline
    from src.storage.database import init_db
    from src.storage.repository import SQLiteRepository

    conn = await init_db(str(tmp_path / "test.db"))
    repo = SQLiteRepository(conn)
    store = SignalStore()

    # Pre-seed unit for EngineRPM
    await store.update("EngineRPM", 0.0, unit="rpm")

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        repository=repo,
        batch_size=1,
        batch_interval_sec=60.0,
    )
    task = asyncio.create_task(pipeline.start())

    raw = RawCANFrame(
        timestamp=time.time(), bus="test", msg_id=1, is_extended=False, is_fd=False, data=bytes(8)
    )
    frame = DecodedFrame(raw=raw, signals={"EngineRPM": 1000.0})
    await queue.put(frame)
    await asyncio.sleep(0.4)

    records = await repo.query_signals(signal_name="EngineRPM")
    assert len(records) >= 1
    assert records[0].unit == "rpm"

    pipeline.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await conn.close()


@pytest.mark.asyncio
async def test_pipeline_stop_while_idle(tmp_path):
    """Pipeline.stop() should exit cleanly even when queue is empty."""
    import asyncio

    from src.core.signal_store import SignalStore
    from src.processor.pipeline import SignalPipeline
    from src.storage.database import init_db
    from src.storage.repository import SQLiteRepository

    conn = await init_db(str(tmp_path / "test.db"))
    repo = SQLiteRepository(conn)
    store = SignalStore()
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        repository=repo,
        batch_size=100,
        batch_interval_sec=60.0,
    )
    task = asyncio.create_task(pipeline.start())
    await asyncio.sleep(0.1)  # let pipeline settle in idle loop
    pipeline.stop()
    # Should exit within 2 seconds (next 1s timeout fires and running=False exits)
    try:
        await asyncio.wait_for(task, timeout=2.5)
    except asyncio.CancelledError:
        pass
    except asyncio.TimeoutError:
        task.cancel()
        pytest.fail("Pipeline did not stop within timeout")
    await conn.close()
