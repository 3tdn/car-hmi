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

    def set_queue_policy(self, policy: str):
        self._state["queue_policy"] = policy


class _FakeRunner:
    def __init__(self):
        self.calls = []

    async def reload_config(self, *, target: str, config=None):
        self.calls.append((target, config))
        return {
            "applied": [target],
            "skipped": [],
            "restart_required": [],
            "errors": [],
        }

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
async def test_list_signals_no_auth(client):
    resp = await client.get("/signals")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_list_signals_with_auth(client):
    resp = await client.get("/signals", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["signal_name"] == "VehicleSpeed"


@pytest.mark.asyncio
async def test_get_signal_not_found(client):
    resp = await client.get("/signals/Unknown", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/system/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_ready_endpoint(client):
    resp = await client.get("/system/ready")
    assert resp.status_code == 200
    assert "ready" in resp.json()


@pytest.mark.asyncio
async def test_health_endpoint_error_on_reader_fatal():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    now = time.time()
    app = create_app(
        store,
        _FakeRepo(),
        can_readers=[_FakeReader(thread_alive=False, last_frame_timestamp=now - 120.0, fatal_error="reconnect_failed")],
        api_key="",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/system/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_ready_false_when_reader_frames_stale():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    now = time.time()
    app = create_app(
        store,
        _FakeRepo(),
        can_readers=[_FakeReader(thread_alive=True, last_frame_timestamp=now - 120.0, fatal_error=None)],
        api_key="",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/system/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert data["details"]["readers_recent_frames"] is False


@pytest.mark.asyncio
async def test_list_alarms(client):
    resp = await client.get("/alarms", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_alarm_by_id(client):
    resp = await client.get("/alarms/1", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["signal_name"] == "CoolantTemp"

    resp_not_found = await client.get("/alarms/99", headers={"X-API-Key": "test-key"})
    assert resp_not_found.status_code == 404


# ── System Metrics tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_metrics_endpoint(client):
    """GET /system/metrics trả về JSON đầy đủ thông tin tài nguyên."""
    resp = await client.get("/system/metrics")
    assert resp.status_code == 200
    data = resp.json()

    # CPU fields
    assert "cpu_percent" in data
    assert isinstance(data["cpu_percent"], (int, float))
    assert "cpu_percent_per_core" in data
    assert isinstance(data["cpu_percent_per_core"], list)
    assert data["cpu_count_logical"] > 0

    # RAM fields
    assert data["ram_total_mb"] > 0
    assert "ram_percent" in data

    # Process fields
    assert data["process_pid"] > 0
    assert data["process_memory_rss_mb"] >= 0

    # Disk fields
    assert data["disk_total_gb"] > 0

    # Application-specific
    assert "queue_size" in data
    assert "queue_maxsize" in data
    assert "heap_allocated_mb" in data
    assert "gc_objects" in data
    assert "asyncio_tasks" in data
    assert "python_version" in data
    assert "platform" in data
    assert data["timestamp"] > 0


@pytest.mark.asyncio
async def test_system_metrics_cpu_cores(client):
    """cpu_percent_per_core phải có đúng số phần tử = cpu_count_logical."""
    resp = await client.get("/system/metrics")
    data = resp.json()
    assert len(data["cpu_percent_per_core"]) == data["cpu_count_logical"]


@pytest.mark.asyncio
async def test_system_metrics_no_auth_required(client):
    """System metrics endpoint không yêu cầu auth (giống /health)."""
    resp = await client.get("/system/metrics")
    assert resp.status_code == 200


# ── Available signals endpoint tests ────────────────────────────────────────


@pytest.mark.asyncio
async def test_available_signals_requires_auth(client):
    resp = await client.get("/signals/available")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_available_signals_returns_metadata(client):
    """GET /signals/available trả về metadata đầy đủ cho mỗi signal."""
    resp = await client.get("/signals/available", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert "signals_info" in data
    assert "total" in data
    assert data["total"] >= 1
    # VehicleSpeed should be present from the fixture
    names = [item["signal_name"] for item in data["signals_info"]]
    assert "VehicleSpeed" in names
    vs = next(i for i in data["signals_info"] if i["signal_name"] == "VehicleSpeed")
    assert vs["value"] == 60.0
    # Metadata fields should exist (even if None)
    assert "unit" in vs
    assert "writable" in vs
    assert "alarm_warning_high" in vs


# ── APIKeyAuth.verify() unit tests ──────────────────────────────────────────


def test_api_key_auth_verify_valid():
    """verify() returns True for correct key."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("my-secret")
    assert auth.verify("my-secret") is True


def test_api_key_auth_verify_invalid():
    """verify() returns False for wrong or missing key."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("my-secret")
    assert auth.verify("wrong") is False
    assert auth.verify(None) is False
    assert auth.verify("") is False


def test_api_key_auth_verify_disabled():
    """verify() always returns True when auth is disabled (empty key)."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("")
    assert auth.verify(None) is True
    assert auth.verify("anything") is True


# ── Config reset endpoint test ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reset_general_config(monkeypatch):
    """POST /config/general/reset calls write_default_bus and returns ok + default dict."""
    import src.core.config_manager as cm

    default_payload = {"can": [{"interface": "virtual", "channel": "vcan0"}], "api": {}}
    monkeypatch.setattr(cm, "write_default_bus", lambda path=None: default_payload)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.backup_manager = BackupManager(base_dir=Path("tmp/test-backups-reset"), retention_count=20)
    app.state.config_manager = ConfigReloadManager(backup_manager=app.state.backup_manager)
    app.state.runner = _FakeRunner()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/config/general/reset", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "default" in data
    assert data["default"]["can"][0]["interface"] == default_payload["can"][0]["interface"]
    assert data["default"]["can"][0]["channel"] == default_payload["can"][0]["channel"]
    assert data["reload"]["ok"] is True


@pytest.mark.asyncio
async def test_patch_general_config_returns_reload_status(client):
    resp = await client.patch(
        "/config/general",
        headers={"X-API-Key": "test-key"},
        json={"processor": {"queue_policy": "drop_oldest"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["reload"]["ok"] is True
    assert data["reload"]["target"] == "general"


@pytest.mark.asyncio
async def test_post_alarms_config_requires_valid_shape(client):
    resp = await client.post("/config/alarms", headers={"X-API-Key": "test-key"}, json={"foo": "bar"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_alarms_config_returns_reload_status(client):
    resp = await client.post(
        "/config/alarms",
        headers={"X-API-Key": "test-key"},
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


# ── WebSocket auth tests ─────────────────────────────────────────────────────


def test_ws_auth_rejected_without_key():
    """WebSocket connection is rejected when auth is enabled and no key is provided."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="ws-secret")
    with TestClient(app, raise_server_exceptions=False) as sc:
        with pytest.raises(Exception):
            with sc.websocket_connect("/ws/signals") as ws:
                ws.receive_text()


def test_ws_auth_accepted_with_valid_key():
    """WebSocket connection succeeds with valid API key in query string."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="ws-secret")
    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/signals?api_key=ws-secret") as ws:
            pass  # connection established — no exception raised


def test_ws_no_auth_when_disabled():
    """WebSocket connects freely when auth is disabled (empty api_key)."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/signals") as ws:
            pass  # should connect without any key


def test_ws_subscribe_signal_payload_format():
    """WS signal frame uses timestamp + signals[{name,std_name,value}] format."""
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    mgr = app.state.ws_manager

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["VehicleSpeed"]}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "subscribe_ack"

            import asyncio

            asyncio.run(mgr.broadcast_signal("VehicleSpeed", 23.0, 1717243200.123))
            frame = json.loads(ws.receive_text())

            assert "timestamp" in frame
            assert isinstance(frame.get("signals"), list)
            assert len(frame["signals"]) == 1
            sig = frame["signals"][0]
            assert set(sig.keys()) == {"name", "std_name", "value"}
            assert sig["name"] == "VehicleSpeed"
            assert sig["std_name"] == "VehicleSpeed"
            assert sig["value"] == 23.0
