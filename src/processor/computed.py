"""Tín hiệu tính toán / ảo dẫn xuất từ giá trị tín hiệu thô."""

from __future__ import annotations

import logging
from collections.abc import Callable

from src.processor.pipeline import ProcessingStage

logger = logging.getLogger(__name__)


class ComputedSignals(ProcessingStage):
    """Tính toán tín hiệu ảo từ công thức áp dụng cho tín hiệu hiện có.

    Ví dụ:
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
