"""Integration tests — end-to-end pipeline: CAN bus → reader → pipeline → storage."""

from __future__ import annotations

import asyncio
import json
import time

import can
import pytest

from src.can_io.parser import DatabaseLoader
from src.can_io.reader import CANReader, DecodedFrame
from src.can_io.writer import CANWriter
from src.core.signal_store import SignalStore
from src.processor.alarms import AlarmChecker, AlarmConfig
from src.processor.computed import ComputedSignals
from src.processor.pipeline import SignalPipeline
from src.storage.database import init_db
from src.storage.repository import SQLiteRepository


@pytest.fixture
def json_db_file(tmp_path):
    """Create a test CAN DB JSON file in tmp_path."""
    db_file = tmp_path / "test.json"
    db_file.write_text(
        json.dumps(
            {
                "messages": {
                    "ECM_Status": {
                        "id": 100,
                        "dlc": 8,
                        "signals": {
                            "EngineRPM": {
                                "start_bit": 0,
                                "length": 16,
                                "factor": 0.25,
                                "offset": 0,
                                "unit": "rpm",
                                "is_signed": False,
                                "byte_order": "little_endian",
                            },
                            "CoolantTemp": {
                                "start_bit": 16,
                                "length": 8,
                                "factor": 1.0,
                                "offset": -40,
                                "unit": "degC",
                                "is_signed": False,
                                "byte_order": "little_endian",
                            },
                        },
                    }
                }
            }
        )
    )
    return db_file


@pytest.fixture
def db_loader(json_db_file):
    loader = DatabaseLoader()
    loader.add_paths([str(json_db_file)])
    return loader


@pytest.mark.asyncio
async def test_pipeline_processes_frame(tmp_path, db_loader):
    """Pipeline receives a DecodedFrame and updates SignalStore + SQLite."""
    # Setup storage
    conn = await init_db(str(tmp_path / "test.db"))
    repo = SQLiteRepository(conn)
    store = SignalStore()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        repository=repo,
        batch_size=1,  # flush every record for testing
        batch_interval_sec=60.0,
    )

    # Start pipeline
    task = asyncio.create_task(pipeline.start())

    # Push a decoded frame into the queue
    from src.can_io.reader import RawCANFrame

    raw = RawCANFrame(
        timestamp=time.time(), bus="test", msg_id=100, is_extended=False, is_fd=False, data=bytes(8)
    )
    frame = DecodedFrame(raw=raw, signals={"EngineRPM": 1500.0, "CoolantTemp": 85.0})
    await queue.put(frame)

    # Wait for pipeline to process
    await asyncio.sleep(0.5)

    # Check SignalStore
    rpm = await store.get("EngineRPM")
    assert rpm is not None
    assert rpm.value == pytest.approx(1500.0)

    temp = await store.get("CoolantTemp")
    assert temp is not None
    assert temp.value == pytest.approx(85.0)

    # Check SQLite
    records = await repo.query_signals(signal_name="EngineRPM")
    assert len(records) >= 1
    assert records[0].value == pytest.approx(1500.0)

    pipeline.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await conn.close()


@pytest.mark.asyncio
async def test_pipeline_with_alarm(tmp_path, db_loader):
    """AlarmChecker fires alarm when threshold exceeded; alarm stored in DB."""
    conn = await init_db(str(tmp_path / "test.db"))
    repo = SQLiteRepository(conn)
    store = SignalStore()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        repository=repo,
        batch_size=1,
        batch_interval_sec=60.0,
    )

    # Add alarm checker: CoolantTemp critical ≥ 110
    fired_alarms = []

    async def alarm_recorder(alarm):
        fired_alarms.append(alarm)

    checker = AlarmChecker([AlarmConfig(signal="CoolantTemp", critical_high=110.0)])
    checker.add_alarm_handler(alarm_recorder)
    pipeline.add_stage(checker)

    task = asyncio.create_task(pipeline.start())

    # Send a frame with CoolantTemp=120 → should trigger critical alarm
    from src.can_io.reader import RawCANFrame

    raw = RawCANFrame(
        timestamp=time.time(), bus="test", msg_id=100, is_extended=False, is_fd=False, data=bytes(8)
    )
    frame = DecodedFrame(raw=raw, signals={"CoolantTemp": 120.0})
    await queue.put(frame)
    await asyncio.sleep(0.5)

    assert len(fired_alarms) == 1
    assert fired_alarms[0].level == "critical"
    assert fired_alarms[0].signal == "CoolantTemp"

    pipeline.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await conn.close()


@pytest.mark.asyncio
async def test_pipeline_with_computed_signals(tmp_path, db_loader):
    """ComputedSignals stage adds derived signals."""
    conn = await init_db(str(tmp_path / "test.db"))
    repo = SQLiteRepository(conn)
    store = SignalStore()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        repository=repo,
        batch_size=10,
        batch_interval_sec=60.0,
    )
    pipeline.add_stage(ComputedSignals({"DoubleRPM": lambda s: s.get("EngineRPM", 0) * 2}))

    task = asyncio.create_task(pipeline.start())

    from src.can_io.reader import RawCANFrame

    raw = RawCANFrame(
        timestamp=time.time(), bus="test", msg_id=100, is_extended=False, is_fd=False, data=bytes(8)
    )
    frame = DecodedFrame(raw=raw, signals={"EngineRPM": 3000.0})
    await queue.put(frame)
    await asyncio.sleep(0.5)

    doubled = await store.get("DoubleRPM")
    assert doubled is not None
    assert doubled.value == pytest.approx(6000.0)

    pipeline.stop()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await conn.close()


@pytest.mark.asyncio
async def test_e2e_writer_to_reader(json_db_file):
    """Full loop: writer sends encoded frame → virtual bus → reader decodes it."""
    bus_tx = can.Bus(interface="virtual", channel="e2e_test", receive_own_messages=False)
    bus_rx = can.Bus(interface="virtual", channel="e2e_test", receive_own_messages=False)

    loader = DatabaseLoader()
    loader.add_paths([str(json_db_file)])

    writer = CANWriter(bus=bus_tx, db=loader)
    queue: asyncio.Queue[DecodedFrame] = asyncio.Queue(maxsize=10)
    reader = CANReader(bus=bus_rx, db=loader, queue=queue)

    reader_task = asyncio.create_task(reader.start())

    # Write a signal
    await writer.send_signal("EngineRPM", 2000.0)

    # Reader should decode it
    frame = await asyncio.wait_for(queue.get(), timeout=3.0)
    assert "EngineRPM" in frame.signals
    assert frame.signals["EngineRPM"] == pytest.approx(2000.0, abs=0.5)

    reader.stop()
    reader_task.cancel()
    try:
        await reader_task
    except asyncio.CancelledError:
        pass
    bus_tx.shutdown()
    bus_rx.shutdown()


@pytest.mark.asyncio
async def test_pipeline_flush_on_shutdown(tmp_path, db_loader):
    """Pipeline.flush() writes remaining buffer to DB."""
    conn = await init_db(str(tmp_path / "test.db"))
    repo = SQLiteRepository(conn)
    store = SignalStore()

    queue: asyncio.Queue = asyncio.Queue(maxsize=10)
    pipeline = SignalPipeline(
        input_queue=queue,
        signal_store=store,
        repository=repo,
        batch_size=9999,  # large batch → no auto-flush
        batch_interval_sec=9999,
    )
    task = asyncio.create_task(pipeline.start())

    from src.can_io.reader import RawCANFrame

    for i in range(5):
        raw = RawCANFrame(
            timestamp=time.time(),
            bus="test",
            msg_id=100,
            is_extended=False,
            is_fd=False,
            data=bytes(8),
        )
        frame = DecodedFrame(raw=raw, signals={f"sig_{i}": float(i)})
        await queue.put(frame)

    await asyncio.sleep(0.5)

    # Nothing flushed yet (batch threshold not reached)
    records = await repo.query_signals(limit=100)
    assert len(records) == 0

    # Trigger graceful flush
    pipeline.stop()
    await pipeline.flush()

    records = await repo.query_signals(limit=100)
    assert len(records) == 5

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await conn.close()
