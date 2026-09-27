"""Regression coverage for exception and resource cleanup boundaries."""

import asyncio
import json
from unittest.mock import Mock

import can
import pytest

from src.can_io.parser import DatabaseLoader
from src.can_io.writer import CANWriter, CANWriterRouter
from src.core.config import AppConfig, WriterConfig
from src.core.runner import AppRunner
from src.core.signal_store import SignalStore
from src.processor.pipeline import SignalPipeline


@pytest.fixture
def failure_db(tmp_path):
    path = tmp_path / "can.json"
    path.write_text(json.dumps({"messages": {
        name: {"id": msg_id, "size": 1, "signals": {
            name: {"start_bit": 0, "length": 8, "factor": 1, "offset": 0}
        }} for msg_id, name in [(100, "First"), (101, "Second")]
    }}))
    db = DatabaseLoader()
    db.load(path)
    return db


async def test_batch_reports_frames_sent_before_transport_failure(failure_db):
    bus = Mock()
    bus.send.side_effect = [None, can.CanError("disconnected")]
    store = SignalStore()
    writer = CANWriter(bus, failure_db, signal_store=store)
    router = CANWriterRouter()
    router.register(failure_db, writer)

    sent, errors = await router.send_signals_batch({"First": 12.0, "Second": 34.0})
    await asyncio.sleep(0)
    assert sent == {"First": 12.0}
    assert [error["signal_name"] for error in errors] == ["Second"]
    assert errors[0]["kind"] == "transport"
    assert (await store.get("First")).value == 12.0
    assert await store.get("Second") is None
    assert bytes(bus.send.call_args_list[0].args[0].data) == b"\x0c"


@pytest.mark.parametrize("value, payload", [
    (float("nan"), b"\x00"), (float("inf"), b"\x00"),
    (-float("inf"), b"\x00"), (256, b"\x00"), (-1, b"\xff"),
])
async def test_encoder_warns_and_preserves_legacy_payload(failure_db, value, payload, caplog):
    bus = Mock()
    writer = CANWriter(bus, failure_db)
    await writer.send_signal("First", value)
    assert bytes(bus.send.call_args.args[0].data) == payload
    assert "First" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)


async def test_api_partial_success_is_warning_and_overflow_keeps_legacy_encoding(failure_db):
    from httpx import ASGITransport, AsyncClient

    from src.api.app import create_app

    bus = Mock()
    bus.send.side_effect = [None, can.CanError("disconnected")]
    writer = CANWriter(bus, failure_db)
    router = CANWriterRouter()
    router.register(failure_db, writer)
    app = create_app(SignalStore(), api_key="test-key")
    app.state.writer = router
    headers = {"X-API-Key": "test-key", "X-Dev-Mode": "true"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        partial = await client.post("/signals/batch_update", headers=headers, json={
            "signals": [
                {"signal_name": "First", "value": 12},
                {"signal_name": "Second", "value": 34},
            ],
        })
        assert partial.status_code == 202
        assert partial.json()["queued"] == [{"signal_name": "First", "value": 12}]
        assert partial.json()["errors"] == []
        warning = partial.json()["warnings"][0]
        assert warning["signal_name"] == "Second"
        assert warning["code"] == "can_write_partial"
        assert "disconnected" in warning["message"]
        bus.send.side_effect = None
        overflow = await client.put("/signals/First", headers=headers, json={"value": 256})
        assert overflow.status_code == 202
        overflow_batch = await client.post("/signals/batch_update", headers=headers, json={
            "signals": [{"signal_name": "First", "value": 256}],
        })
        assert overflow_batch.status_code == 202
        assert bus.send.call_count == 4

        bus.send.side_effect = can.CanError("disconnected")
        failed = await client.post("/signals/batch_update", headers=headers, json={
            "signals": [{"signal_name": "First", "value": 1}],
        })
        assert failed.status_code == 503


async def test_pipeline_cancellation_does_not_leave_queue_consumer():
    queue = asyncio.Queue()
    pipeline = SignalPipeline(queue, SignalStore())
    task = asyncio.create_task(pipeline.start())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    queue.put_nowait("next frame")
    await asyncio.sleep(0)
    assert queue.get_nowait() == "next frame"


async def test_periodic_transport_failure_is_logged_and_consumed(failure_db, caplog):
    bus = Mock()
    bus.send.side_effect = [None, can.CanError("link lost")]
    writer = CANWriter(bus, failure_db, writer_config=WriterConfig(
        periodic_mode=True, periodic_time_step=1, periodic_duration=1000,
    ))
    await writer.send_signal("First", 1)
    task = next(iter(writer._periodic_tasks.values()))
    result = await asyncio.gather(task, return_exceptions=True)
    assert result == [None]
    assert "link lost" in caplog.text
    assert not writer._periodic_tasks


async def test_watchdog_fatal_failure_reaches_runtime_supervisor():
    runner = AppRunner(AppConfig())
    runner.config.supervisor.watchdog_interval_sec = 0.001
    reader = Mock()
    reader.get_runtime_state.return_value = {
        "thread_alive": False, "fatal_error": "recv_thread_stuck",
        "last_recv_age_sec": None,
    }
    runner._readers = [reader]
    runner._tasks = [asyncio.create_task(runner._watchdog(), name="watchdog")]
    with pytest.raises(RuntimeError, match="Unrecoverable CAN reader failure"):
        await asyncio.wait_for(runner._wait_for_runtime_tasks(tuple(runner._tasks)), 1)
