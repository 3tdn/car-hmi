"""Kho lưu trữ trạng thái tín hiệu trong bộ nhớ (mẫu Observer / Pub-Sub)."""

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
    status: str = "ok"  # ok | warning | critical  (trạng thái: bình thường | cảnh báo | nguy hiểm)
    timestamp: float = 0.0
    unit: str | None = None


class SignalStore:
    """Kho lưu trữ trong bộ nhớ an toàn luồng cho giá trị tín hiệu mới nhất.

    Hỗ trợ đăng ký observer — người đăng ký được thông báo mỗi lần có cập nhật.
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
        """Cập nhật giá trị tín hiệu và thông báo đến tất cả người đăng ký."""
        # Giữ nguyên đơn vị hiện có nếu người gọi không cung cấp
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
        """Cập nhật nhiều tín hiệu trong 1 lần acquire lock — O(1) thay vì O(n) lần."""
        entries: list[tuple[str, SignalValue]] = []
        for name, value in updates.items():
            # Xác định đơn vị: ưu tiên units dict > đơn vị đã lưu > None
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
        """Đọc đồng bộ unit của tín hiệu — an toàn trong CPython nhờ GIL.

        Dùng cho hot-path pipeline để tránh acquire asyncio.Lock() per-signal.
        Unit được khởi tạo lúc seed và không thay đổi trong runtime.
        """
        sv = self._signals.get(name)
        return sv.unit if sv is not None else None

    async def get_snapshot(self) -> dict[str, SignalValue]:
        async with self._lock:
            return dict(self._signals)

    def subscribe(self, callback: Callable[[str, SignalValue], Any]) -> None:
        """Đăng ký callback được gọi mỗi khi có cập nhật tín hiệu."""
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
