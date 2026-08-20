"""Tests for DataExporter (CSV / JSON export)."""

from __future__ import annotations

import csv
import json
import time

import pytest
import pytest_asyncio

from src.storage.database import init_db
from src.storage.exporter import DataExporter
from src.storage.repository import AlarmRecord, SignalRecord, SQLiteRepository


@pytest_asyncio.fixture
async def repo(tmp_path):
    conn = await init_db(str(tmp_path / "export_test.db"))
    yield SQLiteRepository(conn)
    await conn.close()


@pytest_asyncio.fixture
async def exporter(repo):
    return DataExporter(repo)


# ── CSV export ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_signals_csv_empty(exporter, tmp_path):
    out = tmp_path / "signals.csv"
    count = await exporter.export_signals_csv(out)
    assert count == 0
    with out.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    assert len(rows) == 1  # header only
    assert rows[0] == ["signal_name", "value", "unit", "timestamp"]


@pytest.mark.asyncio
async def test_export_signals_csv_with_data(exporter, repo, tmp_path):
    now = time.time()
    await repo.insert_signal(SignalRecord("Speed", 80.0, "km/h", now))
    await repo.insert_signal(SignalRecord("RPM", 3000.0, "rpm", now + 1))

    out = tmp_path / "sub" / "signals.csv"
    count = await exporter.export_signals_csv(out)
    assert count == 2

    with out.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    names = {r["signal_name"] for r in rows}
    assert names == {"Speed", "RPM"}


@pytest.mark.asyncio
async def test_export_signals_csv_filter_by_name(exporter, repo, tmp_path):
    now = time.time()
    await repo.insert_signal(SignalRecord("Speed", 80.0, "km/h", now))
    await repo.insert_signal(SignalRecord("RPM", 3000.0, "rpm", now))

    out = tmp_path / "filtered.csv"
    count = await exporter.export_signals_csv(out, signal_name="Speed")
    assert count == 1


@pytest.mark.asyncio
async def test_export_signals_csv_time_range(exporter, repo, tmp_path):
    t0 = 1_000_000.0
    await repo.insert_signal(SignalRecord("Speed", 60.0, "km/h", t0))
    await repo.insert_signal(SignalRecord("Speed", 80.0, "km/h", t0 + 100))

    out = tmp_path / "range.csv"
    count = await exporter.export_signals_csv(out, start=t0 + 50, end=t0 + 200)
    assert count == 1


# ── JSON export ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_export_alarms_json_empty(exporter, tmp_path):
    out = tmp_path / "alarms.json"
    count = await exporter.export_alarms_json(out)
    assert count == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == []


@pytest.mark.asyncio
async def test_export_alarms_json_with_data(exporter, repo, tmp_path):
    now = time.time()
    await repo.insert_alarm(
        AlarmRecord(None, "CoolantTemp", "critical", 115.0, 110.0, "overheat", now)
    )
    await repo.insert_alarm(AlarmRecord(None, "Speed", "warning", 130.0, 120.0, "fast", now + 1))

    out = tmp_path / "deep" / "alarms.json"
    count = await exporter.export_alarms_json(out)
    assert count == 2

    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 2
    names = {d["signal_name"] for d in data}
    assert names == {"CoolantTemp", "Speed"}
    coolant = next(d for d in data if d["signal_name"] == "CoolantTemp")
    assert coolant["level"] == "critical"
    assert coolant["value"] == 115.0


@pytest.mark.asyncio
async def test_export_alarms_json_filter_by_name(exporter, repo, tmp_path):
    now = time.time()
    await repo.insert_alarm(
        AlarmRecord(None, "CoolantTemp", "critical", 115.0, 110.0, "overheat", now)
    )
    await repo.insert_alarm(AlarmRecord(None, "Speed", "warning", 130.0, 120.0, "fast", now))

    out = tmp_path / "filtered.json"
    count = await exporter.export_alarms_json(out, signal_name="CoolantTemp")
    assert count == 1
