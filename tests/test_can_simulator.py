"""Unit tests for CANSimulator and ScenarioLoader."""

from __future__ import annotations

import asyncio
import json

import can
import pytest

from src.can_io.parser import DatabaseLoader
from src.can_simulator.scenario_loader import ScenarioLoader
from src.can_simulator.simulator import CANSimulator, Scenario, ScenarioStep

# ── ScenarioLoader ────────────────────────────────────────────────────────────


def test_scenario_loader_missing_file(tmp_path):
    loader = ScenarioLoader()
    with pytest.raises(FileNotFoundError):
        loader.load(tmp_path / "nonexistent.yaml")


def test_scenario_loader_valid_flat(tmp_path):
    """Test loading a flat (legacy) scenario format with 't' key."""
    scenario_yaml = tmp_path / "test.yaml"
    scenario_yaml.write_text(
        "name: test\nsteps:\n  - t: 0.0\n    signals:\n      VehicleSpeed: 50.0\n",
        encoding="utf-8",
    )
    loader = ScenarioLoader()
    scenario = loader.load(scenario_yaml)
    assert scenario.name == "test"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].signals["VehicleSpeed"] == pytest.approx(50.0)


def test_scenario_loader_valid_wrapped(tmp_path):
    """Test loading canonical scenario: wrapper format with 'at_sec'."""
    scenario_yaml = tmp_path / "test2.yaml"
    scenario_yaml.write_text(
        "scenario:\n  name: test_wrap\n  duration_sec: 30\n  steps:\n    - at_sec: 0\n      signals:\n        EngineRPM: 800\n",
        encoding="utf-8",
    )
    loader = ScenarioLoader()
    scenario = loader.load(scenario_yaml)
    assert scenario.name == "test_wrap"
    assert scenario.duration_sec == 30
    assert scenario.steps[0].at_sec == 0.0
    assert scenario.steps[0].signals["EngineRPM"] == pytest.approx(800.0)


def test_scenario_loader_json(tmp_path):
    scenario_json = tmp_path / "test.json"
    scenario_json.write_text(
        json.dumps(
            {
                "scenario": {
                    "name": "json_test",
                    "duration_sec": 10,
                    "steps": [
                        {"at_sec": 0, "signals": {"Speed": 0}},
                        {"at_sec": 5, "signals": {"Speed": 60}},
                    ],
                }
            }
        )
    )
    loader = ScenarioLoader()
    scenario = loader.load(scenario_json)
    assert scenario.name == "json_test"
    assert len(scenario.steps) == 2


def test_scenario_loader_auto_duration(tmp_path):
    """Duration auto-calculated from max step time + 5."""
    scenario_yaml = tmp_path / "auto_dur.yaml"
    scenario_yaml.write_text(
        "name: auto\nsteps:\n  - t: 0\n    signals:\n      A: 1\n  - t: 20\n    signals:\n      A: 2\n",
        encoding="utf-8",
    )
    loader = ScenarioLoader()
    scenario = loader.load(scenario_yaml)
    assert scenario.duration_sec == 25  # max_t=20 + 5


# ── CANSimulator ──────────────────────────────────────────────────────────────


@pytest.fixture
def sim_db(tmp_path):
    db_file = tmp_path / "sim.json"
    db_file.write_text(
        json.dumps(
            {
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
                            }
                        },
                    }
                }
            }
        )
    )
    loader = DatabaseLoader()
    loader.add_paths([str(db_file)])
    return loader


@pytest.mark.asyncio
async def test_simulator_sends_frames(sim_db):
    """Simulator encodes and sends a frame for each dirty message."""
    bus = can.Bus(interface="virtual", channel="sim_test", receive_own_messages=True)
    scenario = Scenario(
        name="quick",
        duration_sec=1,
        steps=[ScenarioStep(at_sec=0, signals={"Speed": 42.0})],
    )
    sim = CANSimulator(bus=bus, db=sim_db, scenario=scenario, default_cycle_ms=50, loop=False)
    await sim.start()

    # Read frames that were sent
    msg = bus.recv(timeout=0.5)
    assert msg is not None
    assert msg.arbitration_id == 200
    bus.shutdown()


@pytest.mark.asyncio
async def test_simulator_stop():
    """stop() terminates the simulation loop."""
    bus = can.Bus(interface="virtual", channel="sim_stop")
    loader = DatabaseLoader()
    scenario = Scenario(name="long", duration_sec=999, steps=[])
    sim = CANSimulator(bus=bus, db=loader, scenario=scenario, default_cycle_ms=50)

    task = asyncio.create_task(sim.start())
    await asyncio.sleep(0.2)
    sim.stop()
    await asyncio.wait_for(task, timeout=3.0)
    bus.shutdown()
