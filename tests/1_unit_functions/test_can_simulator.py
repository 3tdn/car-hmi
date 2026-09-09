"""Unit tests for CANSimulator."""

from __future__ import annotations

import asyncio

import can
import pytest

from src.can_simulator.simulator import CANSimulator

# ── CANSimulator ─────────────────────────────────────────────────────────────────────────────




# ── CANSimulator ──────────────────────────────────────────────────────────────

_SAMPLE_DBC = """VERSION ""

NS_ :

BS_:

BU_: ECU1

BO_ 200 TestMsg: 8 ECU1
 SG_ Speed : 0|16@1+ (1,0) [0|200] "km/h" ECU1
"""


@pytest.fixture
def can_dbc_file(tmp_path):
    """Minimal DBC fixture for testing CANSimulator."""
    p = tmp_path / "test.dbc"
    p.write_text(_SAMPLE_DBC, encoding="utf-8")
    return p


@pytest.mark.asyncio
async def test_cansimulator_sends_frames(can_dbc_file):
    """CANSimulator encodes and sends one frame per message per cycle."""
    bus = can.Bus(interface="virtual", channel="sim_test_new", receive_own_messages=True)
    sim = CANSimulator(bus=bus, can_db_file=can_dbc_file, cycle_ms=50, repeat=False)
    await sim.start()

    msg = bus.recv(timeout=0.5)
    assert msg is not None
    assert msg.arbitration_id == 200
    bus.shutdown()


@pytest.mark.asyncio
async def test_cansimulator_stop(can_dbc_file):
    """stop() terminates the CANSimulator loop."""
    bus = can.Bus(interface="virtual", channel="sim_stop_new")
    sim = CANSimulator(bus=bus, can_db_file=can_dbc_file, cycle_ms=50, repeat=True)

    task = asyncio.create_task(sim.start())
    await asyncio.sleep(0.2)
    sim.stop()
    await asyncio.wait_for(task, timeout=3.0)
    bus.shutdown()


def test_cansimulator_missing_dbc(tmp_path):
    """CANSimulator raises FileNotFoundError for a missing DBC file."""
    bus = can.Bus(interface="virtual", channel="sim_missing")
    with pytest.raises(FileNotFoundError):
        CANSimulator(bus=bus, can_db_file=tmp_path / "nonexistent.dbc")
    bus.shutdown()
