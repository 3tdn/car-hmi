"""Calculated / virtual signals derived from raw signal values."""

from __future__ import annotations

import logging
import math
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


class OMSClassificationProcessor(ProcessingStage):
    """Optionally replace OMS classification values with weight-derived classes."""

    def __init__(
        self,
        *,
        bypass_simi_input: bool = False,
        class_config: list[float] | tuple[float, float] = (65.0, 90.0),
        target_signals: dict[str, str] | None = None,
    ) -> None:
        self.apply_runtime_config(
            bypass_simi_input=bypass_simi_input,
            class_config=class_config,
            target_signals=target_signals or {},
        )

    def apply_runtime_config(
        self,
        *,
        bypass_simi_input: bool,
        class_config: list[float] | tuple[float, float],
        target_signals: dict[str, str],
    ) -> None:
        """Replace the active mapping atomically for subsequent signal batches."""
        self._bypass_simi_input = bool(bypass_simi_input)
        self._class_config = (float(class_config[0]), float(class_config[1]))
        self._target_signals = dict(target_signals)

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        if not self._bypass_simi_input:
            return signals

        result = signals
        low, high = self._class_config
        for target_signal, weight_signal in self._target_signals.items():
            if weight_signal not in signals:
                continue
            try:
                weight = float(signals[weight_signal])
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring non-numeric OMS weight signal '%s': %r",
                    weight_signal,
                    signals[weight_signal],
                )
                continue
            if not math.isfinite(weight):
                logger.warning(
                    "Ignoring non-finite OMS weight signal '%s': %r",
                    weight_signal,
                    signals[weight_signal],
                )
                continue
            if result is signals:
                result = dict(signals)
            if weight < low:
                result[target_signal] = 0.0
            elif weight <= high:
                result[target_signal] = 1.0
            else:
                result[target_signal] = 2.0

        return result


class HBStateNormalizer(ProcessingStage):
    """Remove the protocol offset from received ``HB_State_*`` values.

    HB response states use values 4-7 for the frontend-facing states 0-3.
    Values that are already below the offset are set to 0.
    """

    _SIGNAL_NAMES = (
        "HB_State_FL",
        "HB_State_FR",
        "HB_State_RR1",
        "HB_State_RL1",
        "HB_State_RL2",
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
