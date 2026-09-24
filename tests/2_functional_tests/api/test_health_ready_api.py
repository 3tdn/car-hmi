"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def insert_signal(self, r):
        pass

    async def insert_signals_bulk(self, records):
        pass

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


class _FakeRunner:
    def __init__(self):
        self.retry_calls = 0
        self.reboot_calls = 0
        self.frontend_activity_calls = 0

    def notify_frontend_activity(self):
        self.frontend_activity_calls += 1
        return 1

    async def retry_can_connections(self):
        self.retry_calls += 1
        return [True, False]

    async def request_reboot(self):
        self.reboot_calls += 1
        return True


def _write_profiles(path, *, active, profiles, client_sessions=None, sessions_path=None):
    payload = {
        "active": active,
        "profiles": profiles,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if sessions_path is not None:
        sessions_payload = {"client_sessions": client_sessions or {}}
        sessions_path.write_text(json.dumps(sessions_payload), encoding="utf-8")


@pytest_asyncio.fixture
async def client():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio

async def test_health_endpoint(client):
    resp = await client.get("/system/health")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("ok", "degraded")


async def test_http_activity_notifies_can_reconnect():
    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    runner = _FakeRunner()
    app.state.runner = runner

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        health_response = await client.get("/system/health")
        ready_response = await client.get("/system/ready")
        response = await client.get("/openapi.json")

    assert health_response.status_code == 200
    assert ready_response.status_code == 200
    assert response.status_code == 200
    assert runner.frontend_activity_calls == 1


def test_websocket_connect_and_message_notify_can_reconnect():
    from starlette.testclient import TestClient

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    runner = _FakeRunner()
    app.state.runner = runner

    with TestClient(app) as client, client.websocket_connect("/ws/subscribe") as websocket:
        websocket.send_text(json.dumps({"type": "ping"}))
        assert json.loads(websocket.receive_text()) == {"type": "pong"}

    assert runner.frontend_activity_calls >= 2

async def test_ready_endpoint(client):
    resp = await client.get("/system/ready")
    assert resp.status_code == 200
    assert "ready" in resp.json()

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


async def test_ready_ignores_frame_age_when_stale_detection_is_disabled():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(
        store,
        _FakeRepo(),
        can_readers=[_FakeReader(thread_alive=True, last_frame_timestamp=0.0)],
        api_key="",
    )
    app.state.reader_stale_threshold_sec = 0.0

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/system/ready")

    assert response.status_code == 200
    assert response.json()["ready"] is True


async def test_retry_can_endpoint_requires_auth_and_schedules_reconnect(client):
    runner = _FakeRunner()
    client._transport.app.state.runner = runner

    unauthenticated = await client.post("/system/can/retry")
    assert unauthenticated.status_code == 401

    not_devmode = await client.post("/system/can/retry", headers={"X-API-Key": "test-key"})
    assert not_devmode.status_code == 403

    response = await client.post(
        "/system/can/retry",
        headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
    )
    assert response.status_code == 200
    assert response.json() == {"scheduled": [True, False], "count": 1}
    assert runner.retry_calls == 1


async def test_reboot_endpoint_requires_auth_and_schedules_reboot(client):
    runner = _FakeRunner()
    client._transport.app.state.runner = runner

    unauthenticated = await client.post("/system/reboot")
    assert unauthenticated.status_code == 401

    not_devmode = await client.post("/system/reboot", headers={"X-API-Key": "test-key"})
    assert not_devmode.status_code == 403

    response = await client.post(
        "/system/reboot",
        headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
    )
    assert response.status_code == 202
    assert response.json() == {"status": "reboot_scheduled"}
    assert runner.reboot_calls == 1


async def test_system_controls_are_disabled_without_real_api_key():
    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="change-me-in-production")
    runner = _FakeRunner()
    app.state.runner = runner

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        retry = await client.post(
            "/system/can/retry",
            headers={"X-Dev-Mode": "true"},
        )
        reboot = await client.post(
            "/system/reboot",
            headers={"X-Dev-Mode": "true"},
        )

    assert retry.status_code == 503
    assert reboot.status_code == 503
    assert runner.retry_calls == 0
    assert runner.reboot_calls == 0
