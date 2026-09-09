"""Repository interface and SQLite implementation."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class SignalRecord:
    signal_name: str
    value: float
    unit: str | None
    timestamp: float


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
    """Contract for signal storage operations."""

    @abstractmethod
    async def insert_signal(self, record: SignalRecord) -> None: ...

    @abstractmethod
    async def insert_signals_bulk(self, records: list[SignalRecord]) -> None: ...

    @abstractmethod
    async def query_signals(
        self,
        signal_name: str | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SignalRecord]: ...

    @abstractmethod
    async def delete_old_signals(self, older_than: float) -> int: ...

    @abstractmethod
    async def trim_to_size(self, current_size: int, max_bytes: int, batch_size: int = 5_000) -> int: ...

    @abstractmethod
    async def vacuum(self) -> None: ...

    @abstractmethod
    async def get_signal_config(self, signal_name: str) -> SignalConfigRecord | None: ...

    @abstractmethod
    async def upsert_signal_config(self, record: SignalConfigRecord) -> None: ...


class SQLiteRepository(ISignalRepository):
    """Async SQLite implementation of ISignalRepository."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ── Signal operations ─────────────────────────────────────────────────────

    async def insert_signal(self, record: SignalRecord) -> None:
        await self._conn.execute(
            "INSERT INTO signal_log (signal_name, value, unit, timestamp) VALUES (?,?,?,?)",
            (record.signal_name, record.value, record.unit, record.timestamp),
        )
        await self._conn.commit()

    async def insert_signals_bulk(self, records: list[SignalRecord]) -> None:
        await self._conn.executemany(
            "INSERT INTO signal_log (signal_name, value, unit, timestamp) VALUES (?,?,?,?)",
            [
                (record.signal_name, record.value, record.unit, record.timestamp)
                for record in records
            ],
        )
        await self._conn.commit()

    async def query_signals(
        self,
        signal_name: str | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[SignalRecord]:
        clauses, params = [], []
        if signal_name:
            clauses.append("signal_name = ?")
            params.append(signal_name)
        if start is not None:
            clauses.append("timestamp >= ?")
            params.append(start)
        if end is not None:
            clauses.append("timestamp <= ?")
            params.append(end)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]
        sql = f"SELECT * FROM signal_log {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [SignalRecord(r["signal_name"], r["value"], r["unit"], r["timestamp"]) for r in rows]

    async def delete_old_signals(self, older_than: float) -> int:
        cur = await self._conn.execute("DELETE FROM signal_log WHERE timestamp < ?", (older_than,))
        deleted = cur.rowcount
        await cur.close()
        await self._conn.commit()
        return deleted

    async def trim_to_size(self, current_size: int, max_bytes: int, batch_size: int = 5_000) -> int:
        """Delete the oldest signal_log rows proportionally to bring the DB under max_bytes before VACUUM.

        Algorithm: estimate rows to delete = total_rows x (excess / current_size),
        delete in batches, and return the total number of rows deleted.
        """
        target = int(max_bytes * 0.85)  # target 85% to avoid continuous cleanup churn
        async with self._conn.execute("SELECT COUNT(*) FROM signal_log") as cur:
            total_rows = (await cur.fetchone())[0]
        if total_rows == 0:
            return 0
        excess_fraction = max(0.0, (current_size - target) / current_size)
        rows_to_delete = max(batch_size, int(total_rows * excess_fraction))
        total_deleted = 0
        while total_deleted < rows_to_delete:
            n_batch = min(batch_size, rows_to_delete - total_deleted)
            cur = await self._conn.execute(
                "DELETE FROM signal_log WHERE id IN "
                "(SELECT id FROM signal_log ORDER BY timestamp ASC LIMIT ?)",
                (n_batch,),
            )
            n = cur.rowcount
            await cur.close()
            await self._conn.commit()
            total_deleted += n
            if n == 0:
                break
        return total_deleted

    async def vacuum(self) -> None:
        """Checkpoint WAL and VACUUM to reclaim freed pages and shrink the DB file."""
        await self._conn.commit()

        # The DB may be busy because the pipeline is writing continuously. Retry briefly,
        # then skip the current cycle to avoid repeated traceback logs and runtime impact.
        retries = 3
        for attempt in range(1, retries + 1):
            try:
                async with self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)") as ckpt_cur:
                    await ckpt_cur.fetchall()
                async with self._conn.execute("VACUUM"):
                    pass
                return
            except sqlite3.OperationalError as exc:
                msg = str(exc).lower()
                if "locked" in msg or "statements in progress" in msg:
                    if attempt < retries:
                        await asyncio.sleep(0.2 * attempt)
                        continue
                    logger.warning("Skip VACUUM this cycle: database is busy (%s)", exc)
                    return
                raise

    # ── Config operations ─────────────────────────────────────────────────────

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
        await self._conn.execute(
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
        await self._conn.commit()
