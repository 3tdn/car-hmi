"""Unit tests for signal processing pipeline, filters, and computed signals."""

from __future__ import annotations

import pytest


def test_smoothing_filter_ema_removed_by_policy():
    import src.processor.filters as filters

    assert not hasattr(filters, "SmoothingFilter")


def test_smoothing_filter_moving_avg_removed_by_policy():
    import src.processor.filters as filters

    assert not hasattr(filters, "SmoothingFilter")


@pytest.mark.asyncio
async def test_rate_limiter_drops_fast_updates():
    from src.processor.filters import RateLimiter

    lim = RateLimiter(max_hz=1.0)  # 1 Hz → min 1 s interval
    r1 = await lim.process({"rpm": 1000.0})
    r2 = await lim.process({"rpm": 2000.0})  # arrives instantly → should be dropped
    assert "rpm" in r1
    assert "rpm" not in r2


def test_pipeline_and_rate_limiter_apply_runtime_config():
    import asyncio

    from src.core.signal_store import SignalStore
    from src.processor.filters import RateLimiter
    from src.processor.pipeline import SignalPipeline

    pipeline = SignalPipeline(
        input_queue=asyncio.Queue(maxsize=10),
        signal_store=SignalStore(),
    )
    limiter = RateLimiter(max_hz=10.0)

    pipeline.apply_runtime_config(
        queue_policy="drop_oldest",
        batch_drain_size=99,
    )
    limiter.set_max_hz(25.0)

    assert pipeline._policy == "drop_oldest"
    assert pipeline._batch_drain_size == 99
    assert limiter._min_interval == pytest.approx(0.04)


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
async def test_oms_classification_keeps_can_value_when_bypass_is_disabled():
    from src.processor.computed import OMSClassificationProcessor

    processor = OMSClassificationProcessor(
        bypass_simi_input=False,
        class_config=[65, 90],
        target_signals={
            "OMS_FL_OccupantClassification": "OMS_FL_OccupantWeightMean",
        },
    )
    result = await processor.process(
        {
            "OMS_FL_OccupantClassification": 2.0,
            "OMS_FL_OccupantWeightMean": 50.0,
        }
    )

    assert result["OMS_FL_OccupantClassification"] == pytest.approx(2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("weight", "expected_class"),
    [(64.9, 0.0), (65.0, 1.0), (90.0, 1.0), (90.1, 2.0)],
)
async def test_oms_classification_derives_zero_based_class_from_weight(weight, expected_class):
    from src.processor.computed import OMSClassificationProcessor

    processor = OMSClassificationProcessor(
        bypass_simi_input=True,
        class_config=[65, 90],
        target_signals={
            "OMS_FL_OccupantClassification": "OMS_FL_OccupantWeightMean",
        },
    )
    result = await processor.process(
        {
            "OMS_FL_OccupantClassification": 99.0,
            "OMS_FL_OccupantWeightMean": weight,
        }
    )

    assert result["OMS_FL_OccupantClassification"] == pytest.approx(expected_class)


@pytest.mark.asyncio
async def test_oms_classification_only_updates_target_with_available_weight():
    from src.processor.computed import OMSClassificationProcessor

    processor = OMSClassificationProcessor(
        bypass_simi_input=True,
        class_config=[65, 90],
        target_signals={
            "OMS_FL_OccupantClassification": "OMS_FL_OccupantWeightMean",
            "OMS_FR_OccupantClassification": "OMS_FR_OccupantWeightMean",
        },
    )
    result = await processor.process(
        {
            "OMS_FL_OccupantWeightMean": 70.0,
            "OMS_FR_OccupantClassification": 2.0,
        }
    )

    assert result["OMS_FL_OccupantClassification"] == pytest.approx(1.0)
    assert result["OMS_FR_OccupantClassification"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_oms_classification_pipeline_publishes_derived_value_to_store():
    import asyncio

    from src.core.signal_store import SignalStore
    from src.processor.computed import OMSClassificationProcessor
    from src.processor.pipeline import SignalPipeline

    store = SignalStore()
    pipeline = SignalPipeline(
        input_queue=asyncio.Queue(),
        signal_store=store,
    )
    pipeline.add_stage(
        OMSClassificationProcessor(
            bypass_simi_input=True,
            class_config=[65, 90],
            target_signals={
                "OMS_RL1_OccupantClassification": "OMS_RL1_OccupantWeightMean",
            },
        )
    )

    await pipeline._process_signals({"OMS_RL1_OccupantWeightMean": 91.0})

    value = await store.get("OMS_RL1_OccupantClassification")
    assert value is not None
    assert value.value == pytest.approx(2.0)


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
async def test_pipeline_keeps_latest_signal_value():
    """When multiple updates for the same signal are queued, only the latest value should survive."""
    import asyncio
    import time

    from src.can_io.reader import DecodedFrame, RawCANFrame
    from src.core.signal_store import SignalStore
    from src.processor.pipeline import SignalPipeline
    store = SignalStore()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
    )

    for value in (10.0, 20.0, 30.0):
        raw = RawCANFrame(
            timestamp=time.time(),
            bus="test",
            msg_id=100,
            is_extended=False,
            is_fd=False,
            data=bytes(8),
        )
        await queue.put(DecodedFrame(raw=raw, signals={"EngineRPM": value}))

    task = asyncio.create_task(pipeline.start())

    latest = None
    for _ in range(30):
        latest = await store.get("EngineRPM")
        if latest is not None:
            break
        await asyncio.sleep(0.1)

    assert latest is not None
    assert latest.value == pytest.approx(30.0)

    pipeline.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_pipeline_respects_batch_drain_size():
    """A large backlog should be processed in bounded batches, not drained all at once."""
    import asyncio
    import time

    from src.can_io.reader import DecodedFrame, RawCANFrame
    from src.core.signal_store import SignalStore
    from src.processor.pipeline import ProcessingStage, SignalPipeline

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    store = SignalStore()
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        batch_drain_size=2,
    )
    seen_batches: list[dict[str, float]] = []

    class StopAfterFirstBatch(ProcessingStage):
        async def process(self, signals: dict[str, float]) -> dict[str, float]:
            seen_batches.append(dict(signals))
            pipeline.stop()
            return signals

    pipeline.add_stage(StopAfterFirstBatch())

    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        raw = RawCANFrame(
            timestamp=time.time(),
            bus="test",
            msg_id=100,
            is_extended=False,
            is_fd=False,
            data=bytes(8),
        )
        await queue.put(DecodedFrame(raw=raw, signals={"Speed": value}))

    await asyncio.wait_for(pipeline.start(), timeout=1.0)

    assert seen_batches == [{"Speed": 2.0}]
    assert queue.qsize() == 3


@pytest.mark.asyncio
async def test_pipeline_stop_while_idle():
    """Pipeline.stop() should exit cleanly even when queue is empty."""
    import asyncio

    from src.core.signal_store import SignalStore
    from src.processor.pipeline import SignalPipeline

    store = SignalStore()
    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
    )
    task = asyncio.create_task(pipeline.start())
    await asyncio.sleep(0.1)  # let pipeline settle in idle loop
    pipeline.stop()
    # Should exit within 2 seconds (next 1s timeout fires and running=False exits)
    try:
        await asyncio.wait_for(task, timeout=2.5)
    except asyncio.CancelledError:
        pass
    except TimeoutError:
        task.cancel()
        pytest.fail("Pipeline did not stop within timeout")
