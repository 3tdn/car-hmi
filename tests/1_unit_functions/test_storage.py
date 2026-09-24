"""Integration tests for SQLiteRepository."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from src.storage.database import init_db
from src.storage.repository import SignalRecord, SQLiteRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    conn = await init_db(str(tmp_path / "test.db"))
    yield SQLiteRepository(conn)
    await conn.close()


@pytest.mark.asyncio
async def test_insert_and_query_signal(repo):
    rec = SignalRecord("VehicleSpeed", 80.0, "km/h", time.time())
    await repo.insert_signal(rec)
    results = await repo.query_signals(signal_name="VehicleSpeed")
    assert len(results) == 1
    assert results[0].value == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_query_signals_filters(repo):
    base_ts = time.time()
    for i in range(5):
        await repo.insert_signal(
            SignalRecord("EngineRPM", 1000.0 + i * 100, "rpm", base_ts + i * 10)
        )
    for i in range(3):
        await repo.insert_signal(
            SignalRecord("VehicleSpeed", 50.0 + i * 10, "km/h", base_ts + i * 10)
        )

    # Test filtering by signal_name
    results = await repo.query_signals(signal_name="EngineRPM")
    assert len(results) == 5

    # Test filtering by start and end timestamp
    results = await repo.query_signals(start=base_ts + 15, end=base_ts + 35)
    # The timestamps for EngineRPM are: +0, +10, +20, +30, +40
    # The timestamps for VehicleSpeed are: +0, +10, +20
    # So we should get 2 EngineRPM (+20, +30) and 1 VehicleSpeed (+20) -> total 3
    assert len(results) == 3

    # Test limit and offset
    results = await repo.query_signals(signal_name="EngineRPM", limit=2, offset=1)
    assert len(results) == 2
    # The query is ordered by timestamp DESC, so all 5 EngineRPM records sorted by TS DESC:
    # index 0: +40
    # index 1: +30
    # index 2: +20
    # index 3: +10
    # index 4: +0
    # Limit 2, Offset 1 should return index 1 and 2, which correspond to +30 and +20
    assert results[0].timestamp == pytest.approx(base_ts + 30)
    assert results[1].timestamp == pytest.approx(base_ts + 20)


@pytest.mark.asyncio
async def test_delete_old_signals(repo):
    old_ts = time.time() - 3600
    await repo.insert_signal(SignalRecord("EngineRPM", 1500.0, "rpm", old_ts))
    deleted = await repo.delete_old_signals(older_than=time.time() - 1800)
    assert deleted == 1
