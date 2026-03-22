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
    # buf is [10, 20], length is 2. alpha = 2/3.
    # ema = 2/3 * 20 + 1/3 * 10 = 13.33 + 3.33 = 16.666
    assert result2["speed"] == pytest.approx(16.666666666666664)

    result3 = await f.process({"speed": 30.0})
    # buf is [10, 20, 30], length is 3. alpha = 2/4 = 0.5.
    # ema = 0.5 * 30 + 0.5 * 16.666 = 15 + 8.333 = 23.333
    assert result3["speed"] == pytest.approx(23.333333333333332)

    result4 = await f.process({"speed": 40.0})
    # buf is [10, 20, 30, 40], length is 4. alpha = 2/5 = 0.4.
    # ema = 0.4 * 40 + 0.6 * 23.333 = 16 + 14 = 30.0
    assert result4["speed"] == pytest.approx(30.0)

    result5 = await f.process({"speed": 50.0})
    # buf is [10, 20, 30, 40, 50], length is 5. alpha = 2/6 = 1/3.
    # ema = 1/3 * 50 + 2/3 * 30 = 16.666 + 20 = 36.666
    assert result5["speed"] == pytest.approx(36.66666666666667)

    result6 = await f.process({"speed": 60.0})
    # buf maxes out at 5. length is 5. alpha = 2/6 = 1/3.
    # ema = 1/3 * 60 + 2/3 * 36.666 = 20 + 24.444 = 44.444
    assert result6["speed"] == pytest.approx(44.44444444444445)


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
