"""Smoothing filter and update-rate limiter for the signal processing pipeline."""

from __future__ import annotations

import time

from src.processor.pipeline import ProcessingStage


class RateLimiter(ProcessingStage):
    """Drop updates that arrive faster than max_hz for each signal."""

    def __init__(self, max_hz: float = 10.0) -> None:
        self._min_interval = 1.0 / max_hz
        self._last_update: dict[str, float] = {}

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        now = time.monotonic()
        result = {}
        for name, value in signals.items():
            last = self._last_update.get(name, 0.0)
            if (now - last) >= self._min_interval:
                result[name] = value
                self._last_update[name] = now
        return result
        return result
