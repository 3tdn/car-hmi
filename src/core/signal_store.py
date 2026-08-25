"""In-memory signal state store (Observer / Pub-Sub pattern)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SignalValue:
    value: float
    status: str = "ok"  # ok | warning | critical
    timestamp: float = 0.0
    unit: str | None = None


class SignalStore:
    """Thread-safe in-memory store for the latest signal values.

    Supports observer registration; subscribers are notified on every update.
    """

    def __init__(self) -> None:
        self._signals: dict[str, SignalValue] = {}
        self._subscribers: list[Callable[[str, SignalValue], Any]] = []
        self._lock = asyncio.Lock()

    async def update(
        self,
        name: str,
        value: float,
        status: str = "ok",
        timestamp: float = 0.0,
        unit: str | None = None,
    ) -> None:
        """Update the signal value and notify all subscribers."""
        # Keep the current unit if the caller does not provide one
        existing = self._signals.get(name)
        use_unit = (
            unit if unit is not None else (getattr(existing, "unit", None) if existing else None)
        )
        sv = SignalValue(value=value, status=status, timestamp=timestamp, unit=use_unit)
        async with self._lock:
            self._signals[name] = sv
        await self._notify(name, sv)

    async def bulk_update(
        self,
        updates: dict[str, float],
        status: str = "ok",
        timestamp: float = 0.0,
        units: dict[str, str] | None = None,
    ) -> None:
        """Update multiple signals in one acquire-lock operation — O(1) instead of O(n) calls."""
        entries: list[tuple[str, SignalValue]] = []
        for name, value in updates.items():
            # Determine the unit: prefer units dict > stored unit > None
            existing = self._signals.get(name)
            use_unit = None
            if units and name in units:
                use_unit = units[name]
            elif existing is not None:
                use_unit = getattr(existing, "unit", None)
            sv = SignalValue(value=value, status=status, timestamp=timestamp, unit=use_unit)
            entries.append((name, sv))
        async with self._lock:
            for name, sv in entries:
                self._signals[name] = sv
        for name, sv in entries:
            await self._notify(name, sv)

    async def get(self, name: str) -> SignalValue | None:
        async with self._lock:
            return self._signals.get(name)

    def get_unit(self, name: str) -> str | None:
        """Read the signal unit synchronously — safe in CPython due to the GIL.

        This is used on hot paths to avoid acquiring asyncio.Lock() per signal.
        Units are initialized at seed time and are not expected to change at runtime.
        """
        sv = self._signals.get(name)
        return sv.unit if sv is not None else None

    async def get_snapshot(self) -> dict[str, SignalValue]:
        async with self._lock:
            return dict(self._signals)

    def subscribe(self, callback: Callable[[str, SignalValue], Any]) -> None:
        """Register a callback invoked on every signal update."""
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str, SignalValue], Any]) -> None:
        self._subscribers.remove(callback)

    async def _notify(self, name: str, value: SignalValue) -> None:
        for cb in list(self._subscribers):
            try:
                result = cb(name, value)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                logger.warning("Subscriber callback error for signal '%s': %s", name, exc)
