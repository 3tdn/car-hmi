"""Benchmark: CANSimulator throughput với config/can.json."""

import asyncio
import time

import can

from src.can_simulator.simulator import CANSimulator


async def main() -> None:
    bus = can.Bus(interface="virtual", channel="bench_bus")
    sim = CANSimulator(
        bus=bus,
        can_json_path="config/can.json",
        cycle_ms=50,
        loop=False,
    )

    start = time.perf_counter()
    await sim.start()
    end = time.perf_counter()

    bus.shutdown()
    print(f"Time taken: {end - start:.4f} seconds")


if __name__ == "__main__":
    asyncio.run(main())
