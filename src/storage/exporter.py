"""Export signal data to CSV files."""

from __future__ import annotations

import asyncio
import csv
import logging
from pathlib import Path

from src.storage.repository import ISignalRepository, SignalRecord

logger = logging.getLogger(__name__)


class DataExporter:
    """Export signal records to CSV."""

    def __init__(self, repository: ISignalRepository) -> None:
        self._repo = repository

    async def export_signals_csv(
        self,
        path: str | Path,
        signal_name: str | None = None,
        start: float | None = None,
        end: float | None = None,
        limit: int = 10_000,
    ) -> int:
        records: list[SignalRecord] = await self._repo.query_signals(
            signal_name=signal_name, start=start, end=end, limit=limit
        )
        loop = asyncio.get_running_loop()
        count = await loop.run_in_executor(None, self._write_csv, Path(path), records)
        logger.info("Exported %d signal records to %s", count, path)
        return count

    def _write_csv(self, out: Path, records: list[SignalRecord]) -> int:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["signal_name", "value", "unit", "timestamp"])
            for r in records:
                writer.writerow([r.signal_name, r.value, r.unit, r.timestamp])
        return len(records)
