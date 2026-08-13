"""Alarm detection stage — check signal values against configured thresholds."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.processor.pipeline import ProcessingStage

logger = logging.getLogger(__name__)


@dataclass
class AlarmConfig:
    signal: str
    critical_high: float | None = None
    warning_high: float | None = None
    warning_low: float | None = None
    critical_low: float | None = None


@dataclass
class Alarm:
    signal: str
    level: str  # info | warning | critical
    value: float
    threshold: float
    timestamp: float
    description: str = ""


class AlarmChecker(ProcessingStage):
    """Check each signal value against the configured alarm thresholds.

    Emits Alarm objects to any registered handlers (for example, SignalStore, Storage).
    """

    def __init__(self, configs: list[AlarmConfig]) -> None:
        self._configs = {c.signal: c for c in configs}
        self._alarm_handlers: list = []
        # Track the current alarm state for each signal and only emit when it changes
        # (for example: none -> warning, warning -> critical,
        # critical -> none, ...)
        # Stored as: signal -> (level: str, threshold: float)
        self._last_state: dict[str, tuple[str, float] | None] = {}

    def add_alarm_handler(self, handler) -> None:
        self._alarm_handlers.append(handler)

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        for name, value in signals.items():
            cfg = self._configs.get(name)
            if cfg is None:
                continue
            # Evaluate the current alarm state (None or (level, threshold))
            current = self._eval_state(cfg, value)
            last = self._last_state.get(name)
            # If the state changed, emit an Alarm (or a clear/reset notification)
            if current != last:
                self._last_state[name] = current
                if current is None:
                    # The condition returned to normal — emit a clear/resolved notification
                    ts = time.time()
                    alarm = Alarm(
                        name,
                        "info",
                        value,
                        last[1] if last is not None else 0.0,
                        ts,
                        f"{name} returned to normal",
                    )
                else:
                    level, threshold = current
                    ts = time.time()
                    alarm = Alarm(
                        name,
                        level,
                        value,
                        threshold,
                        ts,
                        f"{name} {('exceeded' if level in ('warning','critical') else 'changed')} threshold ({threshold})",
                    )
                for handler in self._alarm_handlers:
                    await handler(alarm)
        return signals  # pass-through: does not modify the values

    def _check(self, cfg: AlarmConfig, name: str, value: float) -> Alarm | None:
        # Replaced by _eval_state; kept for compatibility but no longer used.
        return None

    def _eval_state(self, cfg: AlarmConfig, value: float) -> tuple[str, float] | None:
        """Evaluate the current alarm state based on the configured thresholds.

        Returns None if there is no active alarm, or (level, threshold) when an
        alarm is active (for example ('warning', 50.0) or ('critical', 100.0)).
        """
        # Prioritize critical warnings before warning-level checks
        if cfg.critical_high is not None and value >= cfg.critical_high:
            return ("critical", cfg.critical_high)
        if cfg.critical_low is not None and value <= cfg.critical_low:
            return ("critical", cfg.critical_low)
        if cfg.warning_high is not None and value >= cfg.warning_high:
            return ("warning", cfg.warning_high)
        if cfg.warning_low is not None and value <= cfg.warning_low:
            return ("warning", cfg.warning_low)
        return None
