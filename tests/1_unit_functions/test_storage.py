"""Unit tests for persistent signal display configuration."""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.storage.database import init_db
from src.storage.repository import SignalConfigRecord, SQLiteRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    conn = await init_db(str(tmp_path / "config.db"))
    yield SQLiteRepository(conn)
    await conn.close()


@pytest.mark.asyncio
async def test_config_database_contains_no_signal_history_table(repo):
    async with repo._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ) as cur:
        tables = {row[0] for row in await cur.fetchall()}

    assert "signal_config" in tables
    assert "signal_log" not in tables


@pytest.mark.asyncio
async def test_upsert_and_get_signal_config(repo):
    record = SignalConfigRecord(
        signal_name="VehicleSpeed",
        unit="km/h",
        min_value=0.0,
        max_value=250.0,
        group_name="Vehicle",
        widget_type="gauge",
        writable=False,
    )

    await repo.upsert_signal_config(record)

    assert await repo.get_signal_config("VehicleSpeed") == record


@pytest.mark.asyncio
async def test_upsert_signal_config_updates_existing_record(repo):
    initial = SignalConfigRecord(
        "CabinTemp", "degC", -40.0, 85.0, "HVAC", "gauge", False
    )
    updated = SignalConfigRecord(
        "CabinTemp", "degC", 16.0, 30.0, "Climate", "slider", True
    )

    await repo.upsert_signal_config(initial)
    await repo.upsert_signal_config(updated)

    assert await repo.get_signal_config("CabinTemp") == updated
    assert await repo.get_signal_config("missing") is None


@pytest.mark.asyncio
async def test_new_config_db_migrates_only_legacy_signal_config(tmp_path):
    legacy_conn = await init_db(str(tmp_path / "signals.db"))
    legacy_repo = SQLiteRepository(legacy_conn)
    record = SignalConfigRecord(
        "VehicleSpeed", "km/h", 0.0, 250.0, "Vehicle", "gauge", False
    )
    await legacy_repo.upsert_signal_config(record)
    await legacy_conn.execute(
        "CREATE TABLE signal_log (id INTEGER PRIMARY KEY, signal_name TEXT)"
    )
    await legacy_conn.execute("INSERT INTO signal_log (signal_name) VALUES ('VehicleSpeed')")
    await legacy_conn.commit()
    await legacy_conn.close()

    config_conn = await init_db(str(tmp_path / "config.db"))
    config_repo = SQLiteRepository(config_conn)
    try:
        assert await config_repo.get_signal_config("VehicleSpeed") == record
        async with config_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}
        assert "signal_log" not in tables
    finally:
        await config_conn.close()
