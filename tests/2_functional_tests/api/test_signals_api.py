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

async def test_get_signal_not_found(client):
    resp = await client.get("/signals/Unknown", headers={"X-API-Key": "test-key"})
    # Quyền profile được kiểm tra trước signal existence => có thể 403 thay vì 404.
    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] in {"profile_not_selected", "profile_signal_denied"}

async def test_available_signals_requires_auth(client):
    resp = await client.get("/signals/available")
    assert resp.status_code == 401

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
    sample = data["signals_info"][0]
    # Metadata fields should exist (even if None)
    assert "unit" in sample
    assert "writable" in sample
    assert "alarm_warning_high" in sample

async def test_write_signal_requires_write_permission(monkeypatch, tmp_path):
    """Signal write bị chặn với profile chỉ có read permission."""
    import src.api.routes.profiles as profile_routes

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
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"},
            json={"value": 77.0},
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert detail["code"] == "profile_permission_denied"
    assert detail["required_permission"] == "write"
    assert detail["profile_name"] == "viewer"

async def test_write_signal_allows_write_permission(monkeypatch, tmp_path):
    """Signal write được phép với profile có write permission."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 77.0},
        )

    assert resp.status_code == 202
    assert app.state.writer.writes == [("VehicleSpeed", 77.0)]

async def test_write_signal_allows_dev_mode_override(monkeypatch, tmp_path):
    """Dev Mode cho phép ghi signal ngoài scope của profile hiện tại."""
    import src.api.routes.profiles as profile_routes

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
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/signals/VehicleSpeed",
            headers={
                "X-API-Key": "test-key",
                "X-Profile-Name": "viewer",
                "X-Dev-Mode": "true",
            },
            json={"value": 77.0},
        )

    assert resp.status_code == 202
    assert app.state.writer.writes == [("VehicleSpeed", 77.0)]

async def test_batch_write_filters_signals_outside_profile_scope(monkeypatch, tmp_path):
    """Batch write chỉ queue signal hợp lệ và trả warnings cho phần bị bỏ qua."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["write"]}],
                "description": "Operator",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/signals/batch_update",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={
                "signals": [
                    {"signal_name": "VehicleSpeed", "value": 80.0},
                    {"signal_name": "FuelLevel", "value": 25.0},
                ]
            },
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["queued"] == [{"signal_name": "VehicleSpeed", "value": 80.0}]
    assert data["warnings"][0]["code"] == "profile_signal_filtered"
    assert data["warnings"][0]["signals"] == ["FuelLevel"]
