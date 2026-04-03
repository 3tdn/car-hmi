"""Entrypoint CLI cho CAN Simulator."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import can

from src.can_io.parser import DatabaseLoader
from src.can_simulator.scenario_loader import ScenarioLoader
from src.can_simulator.simulator import CANSimulator, RandomCANSimulator
from src.core.config import load_config

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="CAN-HMI Simulator")
    parser.add_argument("--config", default="config/system.json")
    parser.add_argument("--scenario", default="scenarios/city_drive.yaml")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    cfg = load_config(args.config)
    # Tải các DBC CAN
    db_loader = DatabaseLoader(format_hint=cfg.can.can_db_format)
    db_loader.add_paths(cfg.can.can_db_files)
    db_loader.add_paths(cfg.can.can_db_dirs)

    # Dùng bus riêng cho simulator
    bus = can.Bus(interface=cfg.can.interface, channel=cfg.can.channel)

    # Ưu tiên cấu hình riêng của simulator nếu có
    sim_conf = Path("src/can_simulator/config.json")
    if sim_conf.exists():
        try:
            import json

            conf = json.loads(sim_conf.read_text(encoding="utf-8"))
        except Exception:
            logging.exception("Failed to read %s", sim_conf)
            conf = {}

        if conf.get("mode", "random") == "random":
            update_hz = float(conf.get("update_hz", 1.0))
            max_delta_pct = float(conf.get("max_delta_percent", 10.0))
            sim = RandomCANSimulator(
                bus=bus,
                db=db_loader,
                default_cycle_ms=cfg.simulator.default_cycle_ms,
                update_hz=update_hz,
                max_delta_percent=max_delta_pct,
                loop=True,
            )
        else:
            scenario = ScenarioLoader().load(args.scenario)
            sim = CANSimulator(
                bus=bus,
                db=db_loader,
                scenario=scenario,
                default_cycle_ms=cfg.simulator.default_cycle_ms,
            )
    else:
        # fallback sang scenario nếu không có cấu hình simulator
        scenario = ScenarioLoader().load(args.scenario)
        sim = CANSimulator(
            bus=bus,
            db=db_loader,
            scenario=scenario,
            default_cycle_ms=cfg.simulator.default_cycle_ms,
        )
    asyncio.run(sim.start())


if __name__ == "__main__":
    main()
