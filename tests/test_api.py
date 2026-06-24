"""Integration tests for REST API endpoints."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
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


@pytest_asyncio.fixture
async def client():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
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
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/config/general/reset", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "default" in data
    assert data["default"] == default_payload


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
