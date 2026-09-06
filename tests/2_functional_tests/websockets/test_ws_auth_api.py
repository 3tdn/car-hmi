"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
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


@pytest_asyncio.fixture
async def client():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio

def test_ws_subscribe_ack_warns_for_signal_outside_profile(monkeypatch, tmp_path):
    """Subscribe ack returns warnings when the client requests a signal outside the profile scope."""
    import src.api.routes.profiles as profile_routes
    from starlette.testclient import TestClient

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="ws-secret")
    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/signals?api_key=ws-secret&profile_name=viewer") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["FuelLevel"]}))
            ack = json.loads(ws.receive_text())

    assert ack["type"] == "subscribe_ack"
    assert ack["channels"] == []
    assert ack["warnings"][0]["code"] == "profile_signal_denied"

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

def test_ws_subscribe_signal_payload_format(monkeypatch, tmp_path):
    """WS signal frame uses timestamp + signals[{name,std_name,value}] format."""
    import src.api.routes.profiles as profile_routes
    from starlette.testclient import TestClient

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="")
    mgr = app.state.ws_manager

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe?profile_name=viewer") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["VehicleSpeed"]}))
            ack = json.loads(ws.receive_text())
            assert ack["type"] == "subscribe_ack"
            assert ack["channels"] == ["VehicleSpeed"]

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
