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
    assert cfg.can_json_path == "config/can.json"


def test_reader_config_defaults():
    from src.core.config import ReaderConfig

    cfg = ReaderConfig()
    assert cfg.frequency_piority == pytest.approx(0.0)
    assert cfg.only_send_signal_update is False


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
            CANConfig(channel="vcan0", can_json_path="config/can.json"),
            CANConfig(channel="vcan1", can_json_path="config/can1.json"),
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
                        "can_json_path": "config/can.json",
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


# ── SignalStore ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_signal_store_update_and_get():
    store = SignalStore()
    await store.update("Speed", 80.0, status="ok", timestamp=1000.0)
    sv = await store.get("Speed")
    assert sv is not None
    assert sv.value == pytest.approx(80.0)
    assert sv.status == "ok"
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
