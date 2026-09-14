"""Tests for core config loading and signal store."""

from __future__ import annotations

import json

import pytest

from src.core.config import AppConfig, CANConfig, load_config
from src.core.signal_store import SignalStore

# ── Config ────────────────────────────────────────────────────────────────────


def test_load_config_from_file():
    """Load the project's system.json and validate it produces AppConfig."""
    from pathlib import Path

    if not Path("config/system.json").exists():
        pytest.skip("config/system.json not found")
    cfg = load_config("config/system.json")
    assert isinstance(cfg, AppConfig)
    assert isinstance(cfg.can, list)
    assert len(cfg.can) >= 1
    assert cfg.can[0].interface == "virtual"
    assert cfg.api.port == 8000
    assert cfg.reader.frequency_piority == pytest.approx(1.0)


def test_can_config_defaults():
    cfg = CANConfig(interface="virtual", channel="vcan0")
    assert cfg.bitrate == 500_000
    assert cfg.can_db_file == "db/can_db/p_v2.dbc"


def test_reader_config_defaults():
    from src.core.config import ReaderConfig

    cfg = ReaderConfig()
    assert cfg.frequency_piority == pytest.approx(0.0)
    assert cfg.only_send_signal_update is False


def test_writer_config_use_prevalue_for_unwritten_signal():
    from src.core.config import WriterConfig

    assert WriterConfig().use_prevalue_for_unwritten_signal is True
    assert (
        WriterConfig(use_prevalue_for_unwritten_signal=False).use_prevalue_for_unwritten_signal
        is False
    )
    with pytest.raises(ValueError):
        WriterConfig(use_prevalue_for_unwritten_signal="invalid")


def test_app_config_can_is_list():
    """AppConfig.can must be a list of CANConfig."""
    app_cfg = AppConfig()
    assert isinstance(app_cfg.can, list)
    assert len(app_cfg.can) == 1
    assert app_cfg.can[0].channel == "vcan0"


def test_app_config_multi_channel():
    """AppConfig accepts multiple CAN channels."""
    app_cfg = AppConfig(
        can=[
            CANConfig(channel="vcan0", can_db_file="db/can_db/p_v2.dbc"),
            CANConfig(channel="vcan1", can_db_file="db/can_db/p_v2.dbc"),
        ]
    )
    assert len(app_cfg.can) == 2
    assert app_cfg.can[1].channel == "vcan1"


def test_app_config_duplicate_channel_rejected():
    """Duplicate channel names must be rejected."""
    with pytest.raises(ValueError, match="Duplicate CAN channel"):
        AppConfig(
            can=[
                CANConfig(channel="vcan0"),
                CANConfig(channel="vcan0"),
            ]
        )


def test_app_config_accepts_auto_for_single_socketcan_channel():
    app_cfg = AppConfig(can=[CANConfig(interface="socketcan", channel="auto")])

    assert app_cfg.can[0].channel == "auto"


def test_app_config_auto_rejects_non_socketcan_interface():
    with pytest.raises(ValueError, match="requires interface 'socketcan'"):
        AppConfig(can=[CANConfig(interface="virtual", channel="auto")])


def test_app_config_auto_rejects_multiple_channels():
    with pytest.raises(ValueError, match="single-channel mode"):
        AppConfig(
            can=[
                CANConfig(interface="socketcan", channel="auto"),
                CANConfig(interface="socketcan", channel="can0"),
            ]
        )


@pytest.mark.asyncio
async def test_runner_continues_startup_when_auto_can_is_unavailable(tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, patch

    import can

    from src.core.runner import AppRunner

    cfg = AppConfig(
        can=[
            CANConfig(
                interface="socketcan",
                channel="auto",
                can_db_file="db/can_db/Interface_Panther_To_CarPC_v8.dbc",
            )
        ],
        simulator={"enabled": False},
        camera={"enabled": False},
        status_monitor={"enabled": False},
        supervisor={"watchdog_interval_sec": 0},
        storage={"sqlite_path": str(tmp_path / "signals.db")},
    )
    runner = AppRunner(cfg)

    with (
        patch(
            "src.can_io.bus_factory.create_bus",
            side_effect=can.CanInitializationError("no UP CAN"),
        ),
        patch.object(
            runner,
            "_build_api_server",
            new=AsyncMock(return_value=None),
        ) as build_api,
    ):
        await runner._init_components(asyncio.get_running_loop())
        await asyncio.sleep(0)

        assert runner._buses == [None]
        assert runner._writers[0]._bus is None
        assert runner._readers[0].get_runtime_state()["reconnecting"] is True
        build_api.assert_awaited_once_with()

        await runner.shutdown()


def test_app_config_empty_can_rejected():
    """Empty CAN list must be rejected."""
    with pytest.raises(ValueError, match="At least one CAN channel"):
        AppConfig(can=[])


def test_load_config_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "nope.json"))


def test_load_config_custom(tmp_path):
    """Load a minimal custom config."""
    cfg_file = tmp_path / "test.json"
    cfg_file.write_text(
        json.dumps(
            {
                "can": [
                    {
                        "interface": "virtual",
                        "channel": "test_ch",
                        "bitrate": 250000,
                        "can_db_file": "db/can_db/p_v2.dbc",
                    }
                ],
                "api": {"host": "127.0.0.1", "port": 9000},
                "storage": {"sqlite_path": str(tmp_path / "test.db")},
                "reader": {"only_send_signal_update": True},
            }
        )
    )
    cfg = load_config(str(cfg_file))
    assert cfg.can[0].bitrate == 250000
    assert cfg.api.port == 9000
    assert cfg.reader.frequency_piority == pytest.approx(0.0)
    assert cfg.reader.only_send_signal_update is True


def test_app_config_accepts_status_monitor_section():
    cfg = AppConfig(
        status_monitor={
            "enabled": True,
            "interval_sec": 10.0,
            "ping_timeout_sec": 1.2,
            "targets": {
                "COM_Status_PumaFLEthernet": "192.168.1.101",
            },
        }
    )
    assert cfg.status_monitor.enabled is True
    assert cfg.status_monitor.interval_sec == pytest.approx(10.0)
    assert cfg.status_monitor.targets["COM_Status_PumaFLEthernet"] == "192.168.1.101"


def test_app_config_accepts_devmode_can_status_bypass():
    cfg = AppConfig(devmode={"bypass_check_CAN_status": True})

    assert cfg.devmode.bypass_check_CAN_status is True


def test_extract_host_supports_raw_ip_host_port_and_url():
    from src.core.runner import _extract_host

    assert _extract_host("192.168.1.100") == "192.168.1.100"
    assert _extract_host("192.168.2.119:8080") == "192.168.2.119"
    assert _extract_host("http://192.168.2.119:8080/stream") == "192.168.2.119"


def test_status_monitor_targets_split_ethernet_and_can_references():
    from src.core.runner import AppRunner

    cfg = AppConfig(
        status_monitor={
            "enabled": True,
            "targets": {
                "COM_Status_PumaFLEthernet": "192.168.1.101",
                "COM_Status_PantherCan": "COM_Status_PumaFLCan",
            },
        }
    )

    runner = AppRunner(cfg)
    ping_targets, can_refs = runner._build_status_targets()

    assert ping_targets["COM_Status_PumaFLEthernet"] == "192.168.1.101"
    assert can_refs["COM_Status_PantherCan"] == "COM_Status_PumaFLCan"


def test_main_exits_with_restart_code_after_api_reboot(monkeypatch):
    import sys

    from src.core import runner as runner_module

    class RebootingRunner:
        reboot_requested = True

        async def start(self):
            return None

    monkeypatch.setattr(runner_module, "load_config", lambda _path: AppConfig())
    monkeypatch.setattr(runner_module, "AppRunner", lambda _cfg: RebootingRunner())
    monkeypatch.setattr(sys, "argv", ["can-hmi"])

    with pytest.raises(SystemExit) as exc_info:
        runner_module.main()

    assert exc_info.value.code == 75


def test_run_linux_restarts_only_after_exit_code_75(tmp_path, monkeypatch):
    import os
    import subprocess
    from pathlib import Path

    if os.name == "nt":
        pytest.skip("run_linux.sh is not used on Windows")

    fake_python = tmp_path / ".venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        """#!/bin/sh
count=0
if [ -f "$CAR_HMI_TEST_COUNTER" ]; then
    count=$(cat "$CAR_HMI_TEST_COUNTER")
fi
count=$((count + 1))
echo "$count" > "$CAR_HMI_TEST_COUNTER"
if [ "$count" -eq 1 ]; then
    exit 75
fi
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    counter = tmp_path / "runner-count"
    monkeypatch.setenv("CAR_HMI_TEST_COUNTER", str(counter))
    script = Path(__file__).resolve().parents[2] / "scripts" / "run_linux.sh"

    result = subprocess.run(
        ["bash", str(script), "unused.json", "INFO", "65432"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0
    assert counter.read_text(encoding="utf-8").strip() == "2"
    assert "restarting in 1 second" in result.stdout


def test_reader_connectivity_allows_disabled_stale_threshold():
    from src.core.runner import AppRunner

    state = {
        "thread_alive": True,
        "fatal_error": None,
        "last_recv_age_sec": None,
    }

    assert AppRunner._reader_state_is_connected(state, 0.0) is True
    assert AppRunner._reader_state_is_connected(state, 30.0) is False


def test_can_status_reference_requires_online_value_when_reference_is_status():
    from src.core.runner import AppRunner
    from src.core.signal_store import SignalValue

    now = 100.0
    offline_status = SignalValue(value=0.0, timestamp=99.0)
    zero_measurement = SignalValue(value=0.0, timestamp=99.0)

    assert (
        AppRunner._can_reference_is_online(
            "COM_Status_PumaFLCan",
            offline_status,
            now=now,
            interval_sec=10.0,
        )
        is False
    )
    assert (
        AppRunner._can_reference_is_online(
            "OMS_FL_HandsOnWheel",
            zero_measurement,
            now=now,
            interval_sec=10.0,
        )
        is True
    )


def test_can_status_signals_are_mapped_to_their_reader_dbc():
    from types import SimpleNamespace

    from src.core.runner import AppRunner

    runner = AppRunner(AppConfig())
    runner._db_loaders = [
        SimpleNamespace(
            signals={
                "COM_Status_PumaFLCan": object(),
                "COM_Status_PumaFLEthernet": object(),
                "SeatPosition": object(),
            }
        ),
        SimpleNamespace(signals={"COM_Status_PumaRRCan": object()}),
    ]

    assert runner._can_status_signals_for_reader(0) == {"COM_Status_PumaFLCan"}
    assert runner._can_status_signals_for_reader(1) == {"COM_Status_PumaRRCan"}
    assert runner._can_status_signals_for_reader(2) == set()


@pytest.mark.asyncio
async def test_disconnected_reader_only_marks_its_channel_status_offline():
    from src.core.runner import AppRunner

    runner = AppRunner(AppConfig())
    await runner.store.bulk_update(
        {
            "COM_Status_PumaFLCan": 1.0,
            "COM_Status_PumaRRCan": 1.0,
        },
        timestamp=10.0,
    )

    await runner._set_can_status_disconnected({"COM_Status_PumaFLCan"})

    front_left = await runner.store.get("COM_Status_PumaFLCan")
    rear_right = await runner.store.get("COM_Status_PumaRRCan")
    assert front_left is not None and front_left.value == 0.0
    assert rear_right is not None and rear_right.value == 1.0


@pytest.mark.asyncio
async def test_system_config_live_reload_synchronizes_runtime_references():
    from types import SimpleNamespace

    from src.core.runner import AppRunner

    class ConfigSink:
        def __init__(self):
            self.calls = []

        def apply_runtime_config(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    class RateSink:
        def __init__(self):
            self.max_hz = None

        def set_max_hz(self, value):
            self.max_hz = value

    class WsSink:
        def __init__(self):
            self.only_updates = None

        def set_only_send_signal_update(self, value):
            self.only_updates = value

    runner = AppRunner(AppConfig())
    pipeline = ConfigSink()
    reader = ConfigSink()
    writer = ConfigSink()
    rate = RateSink()
    websocket = WsSink()
    runner._pipeline = pipeline
    runner._readers = [reader]
    runner._writers = [writer]
    runner._rate_limiter = rate
    runner._ws_manager = websocket
    runner._api_app = SimpleNamespace(state=SimpleNamespace())

    updated = AppConfig(
        processor={
            "max_update_rate_hz": 25.0,
            "queue_policy": "drop_oldest",
            "batch_drain_size": 99,
        },
        storage={"batch_size": 12, "batch_interval_sec": 0.4},
        reader={
            "frequency_piority": 2.0,
            "only_send_signal_update": True,
            "stale_threshold_sec": 8.0,
        },
        writer={
            "periodic_mode": True,
            "periodic_time_step": 40,
            "periodic_duration": 500,
            "use_prevalue_for_unwritten_signal": False,
        },
        devmode={"bypass_check_CAN_status": True},
    )
    changed = [
        "processor.max_update_rate_hz",
        "processor.queue_policy",
        "storage.batch_size",
        "reader.frequency_piority",
        "reader.only_send_signal_update",
        "reader.stale_threshold_sec",
        "writer.periodic_mode",
        "devmode.bypass_check_CAN_status",
    ]

    result = await runner.apply_system_config(updated, changed)

    assert result["applied"] == changed
    assert pipeline.calls[-1][1]["batch_size"] == 12
    assert reader.calls[-1][1]["priority_sec"] == 2.0
    assert writer.calls[-1][0] == (updated.writer,)
    assert rate.max_hz == 25.0
    assert websocket.only_updates is True
    assert runner._api_app.state.reader_stale_threshold_sec == 8.0
    assert runner._api_app.state.devmode_bypass_can_status is True
    assert runner.config is updated

    reboot_config = updated.model_copy(deep=True)
    reboot_config.api.port = 9000
    await runner.apply_system_config(reboot_config, ["api.port"])
    assert runner.pending_reboot_paths == {"api.port"}

    await runner.apply_system_config(updated, ["api.port"])
    assert runner.pending_reboot_paths == set()


# ── SignalStore ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_store_update_and_get():
    store = SignalStore()
    await store.update("Speed", 80.0, timestamp=1000.0)
    sv = await store.get("Speed")
    assert sv is not None
    assert sv.value == pytest.approx(80.0)
    assert sv.timestamp == 1000.0


@pytest.mark.asyncio
async def test_signal_store_get_snapshot():
    store = SignalStore()
    await store.update("A", 1.0)
    await store.update("B", 2.0)
    snap = await store.get_snapshot()
    assert "A" in snap
    assert "B" in snap
    assert snap["A"].value == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_signal_store_subscribe():
    store = SignalStore()
    received = []

    async def callback(name, sv):
        received.append((name, sv.value))

    store.subscribe(callback)
    await store.update("Speed", 42.0)
    assert len(received) == 1
    assert received[0] == ("Speed", 42.0)


@pytest.mark.asyncio
async def test_signal_store_unsubscribe():
    store = SignalStore()
    received = []

    async def callback(name, sv):
        received.append(name)

    store.subscribe(callback)
    await store.update("A", 1.0)
    store.unsubscribe(callback)
    await store.update("B", 2.0)
    assert received == ["A"]


@pytest.mark.asyncio
async def test_signal_store_get_nonexistent():
    store = SignalStore()
    assert await store.get("NoSignal") is None


@pytest.mark.asyncio
async def test_signal_store_update_error_handling():
    import asyncio

    store = SignalStore()
    queue = asyncio.Queue(maxsize=1)

    async def callback(name, sv):
        queue.put_nowait((name, sv.value))

    store.subscribe(callback)

    # Fill the queue
    await store.update("A", 1.0)

    # This update will cause the callback to raise asyncio.QueueFull
    # but the store should catch the exception and update successfully
    await store.update("B", 2.0)

    assert store._signals["A"].value == 1.0
    assert store._signals["B"].value == 2.0


# ── Runner: SystemExit handling ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serve_safe_converts_systemexit():
    """_serve_safe wraps SystemExit into RuntimeError so gather can catch it."""
    import uvicorn

    # Build a server config (not actually used for binding, we monkey-patch serve).
    server_config = uvicorn.Config(
        "src.api.app:create_app",
        host="127.0.0.1",
        port=0,
        log_level="error",
    )
    server = uvicorn.Server(server_config)

    async def _raise_exit():
        raise SystemExit(1)

    server.serve = _raise_exit

    # Replicate the _serve_safe wrapper from runner.py
    async def _serve_safe():
        try:
            await server.serve()
        except SystemExit as exc:
            raise RuntimeError(f"API server exited with code {exc.code}") from exc

    with pytest.raises(RuntimeError, match="API server exited with code 1"):
        await _serve_safe()


# ── SignalStore edge-cases ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_store_unit_preserved_on_update():
    """Unit set on first update should persist across subsequent updates that omit unit."""
    store = SignalStore()
    await store.update("Speed", 60.0, unit="km/h")
    await store.update("Speed", 70.0)  # no unit kwarg — unit must not be erased
    sv = await store.get("Speed")
    assert sv is not None
    assert sv.unit == "km/h"
    assert sv.value == pytest.approx(70.0)


@pytest.mark.asyncio
async def test_signal_store_bulk_update_unit_inheritance():
    """bulk_update without a units dict should preserve units previously stored."""
    store = SignalStore()
    await store.update("RPM", 0.0, unit="rpm")
    await store.update("Temp", 0.0, unit="°C")

    await store.bulk_update({"RPM": 3000.0, "Temp": 90.0}, units=None)

    rpm_sv = await store.get("RPM")
    temp_sv = await store.get("Temp")
    assert rpm_sv.unit == "rpm"
    assert temp_sv.unit == "°C"


@pytest.mark.asyncio
async def test_signal_store_bulk_update_new_units():
    """bulk_update with explicit units dict should set/override units."""
    store = SignalStore()
    await store.bulk_update({"RPM": 1000.0, "Temp": 50.0}, units={"RPM": "rpm", "Temp": "°C"})
    rpm_sv = await store.get("RPM")
    temp_sv = await store.get("Temp")
    assert rpm_sv.unit == "rpm"
    assert temp_sv.unit == "°C"


@pytest.mark.asyncio
async def test_signal_store_concurrent_get():
    """Concurrent get + update should not raise and should return consistent values."""
    import asyncio

    store = SignalStore()
    await store.update("X", 0.0)

    results: list = []

    async def reader():
        for _ in range(50):
            val = await store.get("X")
            results.append(val.value if val else None)
            await asyncio.sleep(0)

    async def writer():
        for i in range(50):
            await store.update("X", float(i))
            await asyncio.sleep(0)

    await asyncio.gather(reader(), writer())
    # All sampled values must be floats (no exception, no None after seeding)
    assert all(v is not None for v in results)


@pytest.mark.asyncio
async def test_signal_store_get_snapshot_isolated():
    """get_snapshot returns a copy — mutating it must not affect the store."""
    store = SignalStore()
    await store.update("A", 1.0)
    snap = await store.get_snapshot()
    # Mutate the snapshot
    from src.core.signal_store import SignalValue

    snap["A"] = SignalValue(value=999.0)
    # Internal store must be unchanged
    sv = await store.get("A")
    assert sv is not None
    assert sv.value == pytest.approx(1.0)
