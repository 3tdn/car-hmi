"""Database initialization for persistent signal display configuration."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

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


async def _migrate_legacy_signal_config(
    conn: aiosqlite.Connection,
    destination: Path,
) -> None:
    """Copy only signal_config from the former signals.db on first startup."""
    if destination.name != "config.db":
        return

    legacy_path = destination.with_name("signals.db")
    if not legacy_path.is_file() or legacy_path.resolve() == destination.resolve():
        return

    async with conn.execute("SELECT COUNT(*) FROM signal_config") as cur:
        if (await cur.fetchone())[0] > 0:
            return

    legacy = None
    try:
        legacy = await aiosqlite.connect(f"{legacy_path.resolve().as_uri()}?mode=ro", uri=True)
        legacy.row_factory = aiosqlite.Row
        async with legacy.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_config'"
        ) as cur:
            if await cur.fetchone() is None:
                return
        async with legacy.execute(
            """SELECT signal_name, unit, min_value, max_value, group_name,
                      widget_type, writable, updated_at
               FROM signal_config"""
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return

        await conn.executemany(
            """INSERT OR IGNORE INTO signal_config
               (signal_name, unit, min_value, max_value, group_name,
                widget_type, writable, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [tuple(row) for row in rows],
        )
        await conn.commit()
        logger.info(
            "Migrated %d signal configuration record(s) from %s",
            len(rows),
            legacy_path,
        )
    except Exception:
        with contextlib.suppress(Exception):
            await conn.rollback()
        logger.warning(
            "Could not migrate signal configuration from %s; continuing with %s",
            legacy_path,
            destination,
            exc_info=True,
        )
    finally:
        if legacy is not None:
            await legacy.close()


async def init_db(path: str) -> aiosqlite.Connection:
    """Open the small configuration database and apply its schema."""
    conn = await aiosqlite.connect(path, timeout=15.0)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA busy_timeout=15000")
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    await _migrate_legacy_signal_config(conn, Path(path))
    logger.info("Signal configuration database initialised at %s", path)
    return conn
