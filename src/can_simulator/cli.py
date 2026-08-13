"""CLI entrypoint for the CAN Simulator."""

from __future__ import annotations

import argparse
import asyncio
import logging

import can

from src.can_simulator.simulator import CANSimulator
from src.core.config import load_config

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="CAN-HMI Simulator")
    parser.add_argument("--config", default="config/system.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    cfg = load_config(args.config)

    # `cfg.can` may be a list of channel configs; use the first one for simulator
    can_cfg = cfg.can[0] if isinstance(cfg.can, list) and cfg.can else cfg.can
    bus = can.Bus(interface=can_cfg.interface, channel=can_cfg.channel)
    sim = CANSimulator(
        bus=bus,
        can_json_path=cfg.simulator.can_json_path,
        cycle_ms=cfg.simulator.default_cycle_ms,
        repeat=True,
        random_mode=cfg.simulator.random_mode,
    )
    asyncio.run(sim.start())


if __name__ == "__main__":
    main()
