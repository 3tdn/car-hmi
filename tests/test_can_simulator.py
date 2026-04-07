"""Unit tests for CANSimulator and ScenarioLoader."""

from __future__ import annotations

import asyncio
import json

import can
import pytest

from src.can_simulator.scenario_loader import ScenarioLoader
from src.can_simulator.simulator import CANSimulator

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
