"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.config_manager import BackupManager, ConfigReloadManager, DBCJobManager
from src.core.devmode_locks import get_seat_lock_registry, reset_seat_lock_registry
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []

    async def get_alarm_by_id(self, alarm_id):
        import time

        from src.storage.repository import AlarmRecord

        if alarm_id == 1:
            return AlarmRecord(
                id=1,
                signal_name="CoolantTemp",
                level="critical",
                value=110.0,
                threshold=100.0,
                description="over temp",
                triggered_at=time.time(),
                acknowledged=False,
                resolved_at=None,
            )
        return None

    async def insert_signal(self, r):
        pass

    async def insert_signals_bulk(self, records):
        pass

    async def insert_alarm(self, a):
        return 1

    async def acknowledge_alarm(self, i):
        return True

    async def resolve_alarm(self, i):
        return True

    async def delete_old_signals(self, o):
        return 0

    async def get_signal_config(self, signal_name):
        return None

    async def upsert_signal_config(self, record):
        pass


class _FakeReader:
    def __init__(self, *, thread_alive: bool, last_frame_timestamp: float, fatal_error: str | None = None):
        self._state = {
            "thread_alive": thread_alive,
            "last_frame_timestamp": last_frame_timestamp,
            "fatal_error": fatal_error,
        }

    def get_runtime_state(self):
        return dict(self._state)


class _FakeWriter:
    def __init__(self):
        self.writes: list[tuple[str, float]] = []

    async def send_signal(self, signal_name, value):
        self.writes.append((signal_name, value))

    async def send_signals_batch(self, values):
        for signal_name, value in values.items():
            self.writes.append((signal_name, value))
        return values, []


def _write_profiles(path, *, active, profiles, client_sessions=None, sessions_path=None):
    payload = {
        "active": active,
        "profiles": profiles,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if sessions_path is not None:
        sessions_payload = {"client_sessions": client_sessions or {}}
        sessions_path.write_text(json.dumps(sessions_payload), encoding="utf-8")


class _FakeRunner:
    def __init__(self, reload_result=None, config=None):
        self.calls = []
        self.reload_result = reload_result or {
            "applied": ["general"],
            "skipped": [],
            "restart_required": [],
            "errors": [],
        }
        self.config = config

    async def reload_config(self, *, target: str, config=None):
        self.calls.append((target, config))
        return self.reload_result

    async def migrate_rx_queue(self, new_maxsize: int, timeout: float = 5.0):
        return {"ok": True, "new_maxsize": new_maxsize, "migrated": 0}


@pytest_asyncio.fixture
async def client():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.backup_manager = BackupManager(base_dir=Path("tmp/test-backups"), retention_count=20)
    app.state.config_manager = ConfigReloadManager(backup_manager=app.state.backup_manager)
    app.state.runner = _FakeRunner()
    app.state.dbc_job_manager = DBCJobManager(work_dir=Path("tmp/test-dbc-jobs"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio

async def test_reset_general_config(monkeypatch, tmp_path):
    """POST /config/general/reset gọi qua ConfigReloadManager và trả về ok + default dict."""
    import src.api.routes.profiles as profile_routes
    from src.core.config_manager import ConfigReloadManager

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    default_payload = {"can": [{"interface": "virtual", "channel": "vcan0"}], "api": {}}

    async def _fake_reset_general_config(self, runtime=None):
        return {"ok": True, "config": default_payload, "applied": [], "skipped": [], "restart_required": [], "errors": []}

    monkeypatch.setattr(ConfigReloadManager, "reset_general_config", _fake_reset_general_config)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.backup_manager = BackupManager(base_dir=tmp_path / "backups", retention_count=20)
    app.state.config_manager = ConfigReloadManager(backup_manager=app.state.backup_manager)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/config/general/reset",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "default" in data
    assert data["default"] == default_payload

async def test_get_signal_config_not_found_returns_structured_error(client):
    resp = await client.get("/config/signal/Unknown", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "signal_config_not_found"
    assert detail["signal_name"] == "Unknown"


@pytest.mark.asyncio
async def test_processor_update_triggers_runtime_reload():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    runner = _FakeRunner()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.backup_manager = BackupManager(base_dir=Path("tmp/test-backups"), retention_count=20)
    app.state.config_manager = ConfigReloadManager(backup_manager=app.state.backup_manager)
    app.state.runner = runner

    config_path = Path("config/system.json")
    original = config_path.read_text(encoding="utf-8")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            resp = await c.post(
                "/config/processor",
                headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
                json={"max_queue_size": 123, "queue_policy": "reject"},
            )
        assert resp.status_code == 200
        assert runner.calls
        assert runner.calls[0][0] == "general"
        assert runner.calls[0][1].processor.max_queue_size == 123
        assert runner.calls[0][1].processor.queue_policy == "reject"
    finally:
        config_path.write_text(original, encoding="utf-8")


@pytest.mark.asyncio
async def test_general_config_patch_returns_runtime_notes(client):
    resp = await client.patch(
        "/config/general",
        headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
        json={"processor": {"max_queue_size": 321}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "runtime_notes" in data
    assert "applied_live" in data["runtime_notes"]
    assert "requires_restart" in data["runtime_notes"]
    assert isinstance(data["runtime_notes"]["requires_restart"], list)


@pytest.mark.asyncio
async def test_general_config_patch_surfaces_restart_required_notes():
    from src.core.config import load_config

    store = SignalStore()
    runner = _FakeRunner(
        reload_result={
            "applied": ["processor.queue_policy"],
            "skipped": [],
            "restart_required": ["api.port"],
            "errors": [],
        },
        config=load_config("config/system.json"),
    )
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.backup_manager = BackupManager(base_dir=Path("tmp/test-backups-runtime-notes"), retention_count=20)
    app.state.config_manager = ConfigReloadManager(backup_manager=app.state.backup_manager)
    app.state.runner = runner

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.patch(
            "/config/general",
            headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
            json={"api": {"port": 9999}},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_notes"]["applied_live"] == ["processor.queue_policy"]
    assert data["runtime_notes"]["requires_restart"] == ["api.port"]


@pytest.mark.asyncio
async def test_get_general_config_prefers_runtime_config():
    from src.core.config import load_config

    store = SignalStore()
    cfg = load_config("config/system.json")
    cfg.api.port = 9090
    cfg.processor.max_queue_size = 777
    runner = _FakeRunner(config=cfg)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.runner = runner

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/config/general", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["api"]["port"] == 9090
    assert data["processor"]["max_queue_size"] == 777


@pytest.mark.asyncio
async def test_list_available_signals_uses_runtime_cache():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.config_snapshot = {"can": [{"can_json_path": "dummy.json"}]}
    app.state.signal_catalog = {
        "VehicleSpeed": {
            "min_value": 0,
            "max_value": 200,
            "unit": "km/h",
            "writable": True,
            "states": None,
        }
    }
    app.state.alarm_snapshot = {"alarms": {"VehicleSpeed": {"warning_high": 100}}}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/signals/available", headers={"X-API-Key": "test-key"})

    assert resp.status_code == 200
    data = resp.json()
    item = next(i for i in data["signals_info"] if i["signal_name"] == "VehicleSpeed")
    assert item["unit"] == "km/h"
    assert item["writable"] is True
    assert item["alarm_warning_high"] == 100


@pytest.mark.asyncio
async def test_patch_general_config_returns_reload_status(client):
    resp = await client.patch(
        "/config/general",
        headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
        json={"processor": {"queue_policy": "drop_oldest"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reload"]["ok"] is True
    assert data["reload"]["target"] == "general"


@pytest.mark.asyncio
async def test_post_alarms_config_requires_valid_shape(client):
    resp = await client.post(
        "/config/alarms",
        headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
        json={"foo": "bar"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_alarms_config_returns_reload_status(client):
    resp = await client.post(
        "/config/alarms",
        headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
        json={"alarms": {"VehicleSpeed": {"warning_high": 100}}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["reload"]["target"] == "alarms"


@pytest.mark.asyncio
async def test_can_info_endpoint(client):
    resp = await client.get("/config/can_info", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert "interfaces" in data
    assert isinstance(data["interfaces"], list)


@pytest.mark.asyncio
async def test_backups_endpoint_list_and_create(client):
    create_resp = await client.post("/config/backups/create", headers={"X-API-Key": "test-key"})
    assert create_resp.status_code == 200
    list_resp = await client.get("/config/backups", headers={"X-API-Key": "test-key"})
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_dbc_upload_and_generate(client):
    dbc_content = b'VERSION ""\nBO_ 100 Example: 8 Vector__XXX\n SG_ Speed : 0|16@1+ (1,0) [0|65535] "km/h" Vector__XXX\n'
    upload_resp = await client.post(
        "/config/dbc/upload",
        headers={"X-API-Key": "test-key"},
        files={"file": ("example.dbc", dbc_content, "text/plain")},
    )
    assert upload_resp.status_code == 200
    job = upload_resp.json()
    result_resp = await client.get(f"/config/dbc/parse_result/{job['id']}", headers={"X-API-Key": "test-key"})
    assert result_resp.status_code == 200
    gen_resp = await client.post(
        "/config/dbc/generate_config",
        headers={"X-API-Key": "test-key"},
        json={"id": job["id"], "output_path": "tmp/test-generated-can.json"},
    )
    assert gen_resp.status_code == 200

