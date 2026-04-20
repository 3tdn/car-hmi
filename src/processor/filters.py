"""Bộ lọc làm mượt và giới hạn tốc độ cập nhật cho pipeline xử lý tín hiệu."""

from __future__ import annotations

import time
from collections import deque

from src.processor.pipeline import ProcessingStage


class SmoothingFilter(ProcessingStage):
    """Áp dụng làm mượt trung bình trượt (hoặc EMA) cho giá trị tín hiệu."""

    def __init__(self, window: int = 5, method: str = "moving_avg") -> None:
        self._window = window
        self._method = method
        self._history: dict[str, deque[float]] = {}
        self._ema_state: dict[str, float] = {}

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        result = {}
        for name, value in signals.items():
            buf = self._history.setdefault(name, deque(maxlen=self._window))
            buf.append(value)
            if self._method == "ema":
                if len(buf) >= 2:
                    alpha = 2.0 / (self._window + 1)
                    if name in self._ema_state:
                        smoothed = alpha * value + (1 - alpha) * self._ema_state[name]
                    else:
                        # Should not happen typically if we started from len 1, but just in case
                        smoothed = sum(buf) / len(buf)
                    self._ema_state[name] = smoothed
                    result[name] = smoothed
                else:
                    self._ema_state[name] = value
                    result[name] = value
            else:
                result[name] = sum(buf) / len(buf)
        return result


class RateLimiter(ProcessingStage):
    """Loại bỏ các cập nhật đến nhanh hơn max_hz cho mỗi tín hiệu."""

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
