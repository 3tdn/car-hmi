"""Calculated / virtual signals derived from raw signal values."""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.processor.pipeline import ProcessingStage

logger = logging.getLogger(__name__)


class ComputedSignals(ProcessingStage):
    """Compute virtual signals from formulas applied to existing signals.

    Example:
        EnginePower_kW = EngineRPM * Torque / 9549
    """

    def __init__(
        self,
        formulas: dict[str, Callable[[dict[str, float]], float]] | None = None,
    ) -> None:
        self._formulas: dict[str, Callable[[dict[str, float]], float]] = formulas or {}

    def add_formula(self, name: str, fn: Callable[[dict[str, float]], float]) -> None:
        self._formulas[name] = fn

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        result = dict(signals)
        for virtual_name, fn in self._formulas.items():
            try:
                result[virtual_name] = fn(signals)
            except Exception as exc:
                logger.debug("Computed signal '%s' failed: %s", virtual_name, exc)
        return result


class HBStateNormalizer(ProcessingStage):
    """Remove the protocol offset from received HB seat-state values.

    HB response states use values 4-7 for the frontend-facing states 0-3.
    Values that are already below the offset are set to 0.
    """

    _SIGNAL_NAMES = (
        "HB_FL_State",
        "HB_FR_State",
        "HB_RR1_State",
        "HB_RL1_State",
        "HB_RL2_State",
    )
    _STATE_OFFSET = 4.0

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        result = signals

        for name in self._SIGNAL_NAMES:
            value = signals.get(name)
            if value is None:
                continue
            if result is signals:
                result = dict(signals)

            result[name] = value - self._STATE_OFFSET if value >= self._STATE_OFFSET else 0

        return result
