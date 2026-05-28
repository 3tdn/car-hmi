"""Giao diện repository và cài đặt SQLite."""

from __future__ import annotations

import logging
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
class AlarmRecord:
    id: int | None
    signal_name: str
    level: str
    value: float
    threshold: float
    description: str
    triggered_at: float
    acknowledged: bool = False
    resolved_at: float | None = None


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
    """Hợp đồng cho tất cả thao tác lưu trữ tín hiệu / cảnh báo."""

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
    async def insert_alarm(self, alarm: AlarmRecord) -> int: ...

    @abstractmethod
    async def query_alarms(
        self,
        signal_name: str | None = None,
        level: str | None = None,
        acknowledged: bool | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlarmRecord]: ...

    @abstractmethod
    async def get_alarm_by_id(self, alarm_id: int) -> AlarmRecord | None: ...

    @abstractmethod
    async def acknowledge_alarm(self, alarm_id: int) -> bool: ...

    @abstractmethod
    async def resolve_alarm(self, alarm_id: int) -> bool: ...

    @abstractmethod
    async def delete_old_signals(self, older_than: float) -> int: ...

    @abstractmethod
    async def get_signal_config(self, signal_name: str) -> SignalConfigRecord | None: ...

    @abstractmethod
    async def upsert_signal_config(self, record: SignalConfigRecord) -> None: ...


class SQLiteRepository(ISignalRepository):
    """Cài đặt async SQLite của ISignalRepository."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    # ── Thao tác tín hiệu ─────────────────────────────────────────────────────

    async def insert_signal(self, record: SignalRecord) -> None:
        await self._conn.execute(
            "INSERT INTO signal_log (signal_name, value, unit, timestamp) VALUES (?,?,?,?)",
            (record.signal_name, record.value, record.unit, record.timestamp),
        )
        await self._conn.commit()

    async def insert_signals_bulk(self, records: list[SignalRecord]) -> None:
        try:
            await self._conn.execute("BEGIN")
            await self._conn.executemany(
                "INSERT INTO signal_log (signal_name, value, unit, timestamp) VALUES (?,?,?,?)",
                [
                    (record.signal_name, record.value, record.unit, record.timestamp)
                    for record in records
                ],
            )
            await self._conn.execute("COMMIT")
        except Exception:
            await self._conn.execute("ROLLBACK")
            raise

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
        await self._conn.commit()
        return cur.rowcount

    # ── Thao tác cảnh báo ──────────────────────────────────────────────────────

    async def insert_alarm(self, alarm: AlarmRecord) -> int:
        cur = await self._conn.execute(
            """INSERT INTO alarm_log
               (signal_name, level, value, threshold, description, triggered_at)
               VALUES (?,?,?,?,?,?)""",
            (
                alarm.signal_name,
                alarm.level,
                alarm.value,
                alarm.threshold,
                alarm.description,
                alarm.triggered_at,
            ),
        )
        await self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def query_alarms(
        self,
        signal_name: str | None = None,
        level: str | None = None,
        acknowledged: bool | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AlarmRecord]:
        clauses, params = [], []
        if signal_name:
            clauses.append("signal_name = ?")
            params.append(signal_name)
        if level:
            clauses.append("level = ?")
            params.append(level)
        if acknowledged is not None:
            clauses.append("acknowledged = ?")
            params.append(int(acknowledged))
        if start is not None:
            clauses.append("triggered_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("triggered_at <= ?")
            params.append(end)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]
        sql = f"SELECT * FROM alarm_log {where} ORDER BY triggered_at DESC LIMIT ? OFFSET ?"
        async with self._conn.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            AlarmRecord(
                r["id"],
                r["signal_name"],
                r["level"],
                r["value"],
                r["threshold"],
                r["description"] or "",
                r["triggered_at"],
                bool(r["acknowledged"]),
                r["resolved_at"],
            )
            for r in rows
        ]

    async def get_alarm_by_id(self, alarm_id: int) -> AlarmRecord | None:
        sql = "SELECT * FROM alarm_log WHERE id = ?"
        async with self._conn.execute(sql, (alarm_id,)) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return AlarmRecord(
            row["id"],
            row["signal_name"],
            row["level"],
            row["value"],
            row["threshold"],
            row["description"] or "",
            row["triggered_at"],
            bool(row["acknowledged"]),
            row["resolved_at"],
        )

    async def acknowledge_alarm(self, alarm_id: int) -> bool:
        cur = await self._conn.execute(
            "UPDATE alarm_log SET acknowledged=1 WHERE id=? AND acknowledged=0", (alarm_id,)
        )
        await self._conn.commit()
        return cur.rowcount > 0

    async def resolve_alarm(self, alarm_id: int) -> bool:
        cur = await self._conn.execute(
            "UPDATE alarm_log SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
            (time.time(), alarm_id),
        )
        await self._conn.commit()
        return cur.rowcount > 0

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
