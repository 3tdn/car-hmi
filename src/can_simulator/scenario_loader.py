"""Bộ tải kịch bản YAML/JSON cho CAN Simulator.

Sơ đồ được hỗ trợ (gói ``scenario:`` theo requirement.md)::

    scenario:
      name: city_drive
      duration_sec: 120
      steps:
        - at_sec: 0
          signals:
            VehicleSpeed: 0
            EngineRPM: 800
        - at_sec: 5
          signals:
            VehicleSpeed: 30
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from src.can_simulator.simulator import Scenario, ScenarioStep


class ScenarioLoader:
    """Tải file kịch bản (YAML hoặc JSON) vào dataclass ``Scenario``.

    Chấp nhận hai biến thể:
    - Khóa cấp cao nhất ``scenario:`` (chuẩn, theo requirement.md)
    - Các khóa phẳng ``name``, ``steps``, … (rút gọn / củ)
    """

    def load(self, path: str | Path) -> Scenario:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Scenario file not found: {path}")

        raw: dict
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)

        # Hỗ trợ cả hai schema gói (``scenario:``) và phẳng
        sc = raw.get("scenario", raw)

        steps = [
            ScenarioStep(
                at_sec=float(s.get("at_sec", s.get("t", 0.0))),
                signals={k: float(v) for k, v in s.get("signals", {}).items()},
            )
            for s in sc.get("steps", [])
        ]
        return Scenario(
            name=sc["name"],
            duration_sec=int(
                sc.get("duration_sec", max((s.at_sec for s in steps), default=60) + 5)
            ),
            steps=sorted(steps, key=lambda x: x.at_sec),
        )
