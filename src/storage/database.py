"""Khởi tạo cơ sở dữ liệu — tạo schema lần đầu chạy."""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS signal_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_name TEXT    NOT NULL,
    value       REAL    NOT NULL,
    unit        TEXT,
    timestamp   REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signal_log_name_ts
    ON signal_log (signal_name, timestamp);

CREATE TABLE IF NOT EXISTS alarm_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_name TEXT    NOT NULL,
    level       TEXT    NOT NULL CHECK (level IN ('info','warning','critical')),
    value       REAL    NOT NULL,
    threshold   REAL    NOT NULL,
    description TEXT,
    triggered_at  REAL NOT NULL,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    resolved_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_alarm_log_name_ts
    ON alarm_log (signal_name, triggered_at);

CREATE TABLE IF NOT EXISTS signal_config (
    signal_name TEXT PRIMARY KEY,
    unit        TEXT,
    min_value   REAL,
    max_value   REAL,
    group_name  TEXT,
    widget_type TEXT,
    writable    INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL    NOT NULL
);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    """Mở (hoặc tạo) cơ sở dữ liệu SQLite và áp dụng schema."""
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    logger.info("Database initialised at %s", path)
    return conn
