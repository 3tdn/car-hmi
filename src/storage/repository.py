"""Repository interface and SQLite implementation for signal configuration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)

_WRITE_RETRIES = 3
_WRITE_RETRY_BASE_SEC = 0.2


@dataclass
class SignalConfigRecord:
    signal_name: str
    unit: str | None
    min_value: float | None
    max_value: float | None
    group_name: str | None
    widget_type: str | None
    writable: bool


class ISignalRepository(ABC):
    """Contract for persistent signal display configuration."""

    @abstractmethod
    async def get_signal_config(self, signal_name: str) -> SignalConfigRecord | None: ...

    @abstractmethod
    async def upsert_signal_config(self, record: SignalConfigRecord) -> None: ...


class SQLiteRepository(ISignalRepository):
    """Async SQLite storage for signal display configuration."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    @staticmethod
    def _is_busy_error(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    async def _run_write(self, sql: str, params) -> int:
        """Execute and commit a write with rollback and bounded busy retries."""
        for attempt in range(1, _WRITE_RETRIES + 1):
            cur = None
            try:
                cur = await self._conn.execute(sql, params)
                affected = cur.rowcount
                await cur.close()
                cur = None
                await self._conn.commit()
                return affected
            except sqlite3.OperationalError as exc:
                if cur is not None:
                    await cur.close()
                with contextlib.suppress(sqlite3.Error):
                    await self._conn.rollback()
                if not self._is_busy_error(exc) or attempt >= _WRITE_RETRIES:
                    raise
                await asyncio.sleep(_WRITE_RETRY_BASE_SEC * attempt)
            except Exception:
                if cur is not None:
                    await cur.close()
                with contextlib.suppress(sqlite3.Error):
                    await self._conn.rollback()
                raise
        raise RuntimeError("unreachable SQLite write retry state")

    async def get_signal_config(self, signal_name: str) -> SignalConfigRecord | None:
        async with self._conn.execute(
            "SELECT * FROM signal_config WHERE signal_name = ?", (signal_name,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return SignalConfigRecord(
            signal_name=row["signal_name"],
            unit=row["unit"],
            min_value=row["min_value"],
            max_value=row["max_value"],
            group_name=row["group_name"],
            widget_type=row["widget_type"],
            writable=bool(row["writable"]),
        )

    async def upsert_signal_config(self, record: SignalConfigRecord) -> None:
        await self._run_write(
            """INSERT INTO signal_config
               (signal_name, unit, min_value, max_value, group_name,
                widget_type, writable, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(signal_name) DO UPDATE SET
               unit=excluded.unit,
               min_value=excluded.min_value,
               max_value=excluded.max_value,
               group_name=excluded.group_name,
               widget_type=excluded.widget_type,
               writable=excluded.writable,
               updated_at=excluded.updated_at""",
            (
                record.signal_name,
                record.unit,
                record.min_value,
                record.max_value,
                record.group_name,
                record.widget_type,
                1 if record.writable else 0,
                time.time(),
            ),
        )
