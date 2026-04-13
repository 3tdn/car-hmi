"""Entrypoint CLI cho CAN Simulator."""

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

    bus = can.Bus(interface=cfg.can.interface, channel=cfg.can.channel)
    sim = CANSimulator(
        bus=bus,
        can_json_path=cfg.simulator.can_json_path,
        cycle_ms=cfg.simulator.default_cycle_ms,
        repeat=True,
    )
    asyncio.run(sim.start())


if __name__ == "__main__":
    main()
