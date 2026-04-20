"""Unit tests for CANSimulator."""

from __future__ import annotations

import asyncio
import json

import can
import pytest

from src.can_simulator.simulator import CANSimulator

# ── CANSimulator ─────────────────────────────────────────────────────────────────────────────




# ── CANSimulator ──────────────────────────────────────────────────────────────


@pytest.fixture
def can_json_file(tmp_path):
    """Minimal can.json fixture for testing CANSimulator."""
    data = {
        "messages": {
            "TestMsg": {
                "id": 200,
                "dlc": 8,
                "signals": {
                    "Speed": {
                        "start_bit": 0,
                        "length": 16,
                        "factor": 1.0,
                        "offset": 0,
                        "unit": "km/h",
                        "is_signed": False,
                        "byte_order": "little_endian",
                        "minimum": 0.0,
                        "maximum": 200.0,
                    }
                },
            }
        }
    }
    p = tmp_path / "can.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_cansimulator_sends_frames(can_json_file):
    """CANSimulator encodes and sends one frame per message per cycle."""
    bus = can.Bus(interface="virtual", channel="sim_test_new", receive_own_messages=True)
    sim = CANSimulator(bus=bus, can_json_path=can_json_file, cycle_ms=50, repeat=False)
    await sim.start()

    msg = bus.recv(timeout=0.5)
    assert msg is not None
    assert msg.arbitration_id == 200
    bus.shutdown()


@pytest.mark.asyncio
async def test_cansimulator_stop(can_json_file):
    """stop() terminates the CANSimulator loop."""
    bus = can.Bus(interface="virtual", channel="sim_stop_new")
    sim = CANSimulator(bus=bus, can_json_path=can_json_file, cycle_ms=50, repeat=True)

    task = asyncio.create_task(sim.start())
    await asyncio.sleep(0.2)
    sim.stop()
    await asyncio.wait_for(task, timeout=3.0)
    bus.shutdown()


def test_cansimulator_missing_json(tmp_path):
    """CANSimulator raises FileNotFoundError for missing can.json."""
    bus = can.Bus(interface="virtual", channel="sim_missing")
    with pytest.raises(FileNotFoundError):
        CANSimulator(bus=bus, can_json_path=tmp_path / "nonexistent.json")
    bus.shutdown()
