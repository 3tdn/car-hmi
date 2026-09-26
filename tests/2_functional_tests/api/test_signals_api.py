"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.can_io.writer import CANWriteRejectedError
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


class _NonTxRejectingWriter:
    error = (
        "CAN write rejected: signal 'OMS_State_Camera' in message "
        "'MON_OMS_State' (msg_id=0xb8) is not TX for local node 'CAR_PC'; "
        "DBC sender(s): [SIMI]"
    )

    async def send_signal(self, signal_name, value):
        raise CANWriteRejectedError(self.error)

    async def send_signals_batch(self, values):
        return {}, [
            {
                "signal_name": signal_name,
                "error": self.error,
                "kind": "not_tx",
            }
            for signal_name in values
        ]


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
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Signal 'Unknown' not found"


@pytest.mark.asyncio
async def test_signal_history_endpoint_is_removed(client):
    response = await client.get(
        "/signals/VehicleSpeed/history",
        headers={"X-API-Key": "test-key"},
    )

    assert response.status_code == 404

async def test_available_signals_requires_auth(client):
    resp = await client.get("/signals/available")
    assert resp.status_code == 401

async def test_available_signals_returns_metadata(client):
    """GET /signals/available returns complete metadata for each signal."""
    resp = await client.get("/signals/available", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    assert "signals_info" in data
    assert "total" in data
    assert data["total"] >= 1
    # VehicleSpeed should be present from the fixture
    names = [item["signal_name"] for item in data["signals_info"]]
    assert "VehicleSpeed" in names
    metadata = {item["signal_name"]: item for item in data["signals_info"]}
    assert metadata["ELK_RL1_LockingRequest"]["writable"] is True
    assert metadata["OMS_State_Camera"]["writable"] is False
    sample = data["signals_info"][0]
    # Metadata fields should exist (even if None)
    assert "unit" in sample
    assert "writable" in sample

async def test_write_signal_requires_write_permission(monkeypatch, tmp_path):
    """Signal writes are blocked for a profile with only read permission."""
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
    """Signal writes are allowed for a profile with write permission."""
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


@pytest.mark.asyncio
async def test_wildcard_full_profile_allows_any_signal(monkeypatch, tmp_path):
    """Profile name '*' with full permission grants access to every signal."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="driver2",
        profiles={
            "driver2": {
                "signals": [{"name": "*", "permission": ["full"]}],
                "description": "Driver view",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        read_resp = await c.get(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "driver2"},
        )
        write_resp = await c.put(
            "/signals/OtherSignal",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "driver2"},
            json={"value": 77.0},
        )

    assert read_resp.status_code == 200
    assert write_resp.status_code == 202
    assert app.state.writer.writes == [("OtherSignal", 77.0)]

async def test_write_signal_allows_dev_mode_override(monkeypatch, tmp_path):
    """Dev Mode allows writing a signal outside the current profile scope."""
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


@pytest.mark.asyncio
async def test_write_signal_rejects_non_tx_message_with_dbc_context():
    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _NonTxRejectingWriter()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/signals/OMS_State_Camera",
            headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
            json={"value": 1.0},
        )

    assert resp.status_code == 403
    detail = resp.json()["detail"]
    assert "message 'MON_OMS_State'" in detail
    assert "msg_id=0xb8" in detail
    assert "DBC sender(s): [SIMI]" in detail


@pytest.mark.asyncio
async def test_batch_write_rejects_non_tx_messages_with_dbc_context():
    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _NonTxRejectingWriter()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/signals/batch_update",
            headers={"X-API-Key": "test-key", "X-Dev-Mode": "true"},
            json={"signals": [{"signal_name": "OMS_State_Camera", "value": 1.0}]},
        )

    assert resp.status_code == 403
    error = resp.json()["detail"][0]
    assert error["kind"] == "not_tx"
    assert "message 'MON_OMS_State'" in error["error"]
    assert "msg_id=0xb8" in error["error"]

async def test_batch_write_filters_signals_outside_profile_scope(monkeypatch, tmp_path):
    """Batch writes queue only valid signals and return warnings for skipped ones."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [
                    {"name": "VehicleSpeed", "permission": ["write"]},
                    {"name": "EngineRPM", "permission": ["full"]},
                    {"name": "CoolantTemp", "permission": ["read"]},
                ],
                "description": "Operator",
            }
        },
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)

    store = SignalStore()
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    app.state.writer.send_signals_batch = AsyncMock(wraps=app.state.writer.send_signals_batch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/signals/batch_update",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={
                "signals": [
                    {"signal_name": "VehicleSpeed", "value": 80.0},
                    {"signal_name": "EngineRPM", "value": 2000.0},
                    {"signal_name": "CoolantTemp", "value": 90.0},
                    {"signal_name": "FuelLevel", "value": 25.0},
                ]
            },
        )

    assert resp.status_code == 202
    data = resp.json()
    assert data["queued"] == [
        {"signal_name": "VehicleSpeed", "value": 80.0},
        {"signal_name": "EngineRPM", "value": 2000.0},
    ]
    assert data["warnings"][0]["code"] == "profile_signal_filtered"
    assert data["warnings"][0]["signals"] == ["CoolantTemp", "FuelLevel"]
    app.state.writer.send_signals_batch.assert_awaited_once_with(
        {"VehicleSpeed": 80.0, "EngineRPM": 2000.0}
    )


@pytest.mark.parametrize(
    ("active", "profile_signals", "requested_profile", "warning_code"),
    [
        ("operator", [{"name": "VehicleSpeed", "permission": ["write"]}], "missing", "profile_not_found"),
        ("missing", [{"name": "VehicleSpeed", "permission": ["write"]}], None, "profile_not_found"),
        (None, [{"name": "VehicleSpeed", "permission": ["write"]}], None, "profile_not_selected"),
        (None, None, None, "profile_not_selected"),
        ("missing", None, "missing", "profile_not_found"),
        ("operator", [], None, "profile_permission_denied"),
        ("operator", [{"name": "VehicleSpeed", "permission": ["read"]}], None, "profile_permission_denied"),
        ("operator", [{"name": "OtherSignal", "permission": ["full"]}], None, "profile_signal_filtered"),
    ],
)
async def test_batch_write_rejects_unresolved_or_disallowed_profile(
    monkeypatch, tmp_path, active, profile_signals, requested_profile, warning_code
):
    """Profile errors and batches without authorized signals never reach the writer."""
    import src.api.routes.profiles as profile_routes

    profiles_path = tmp_path / "profiles.json"
    _write_profiles(
        profiles_path,
        active=active,
        profiles={"operator": {"signals": profile_signals}} if profile_signals is not None else {},
    )
    monkeypatch.setattr(profile_routes, "PROFILES_PATH", profiles_path)
    monkeypatch.setattr(profile_routes, "PROFILE_SESSIONS_PATH", tmp_path / "sessions.json")

    app = create_app(SignalStore(), _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    app.state.writer.send_signals_batch = AsyncMock(wraps=app.state.writer.send_signals_batch)
    headers = {"X-API-Key": "test-key"}
    if requested_profile:
        headers["X-Profile-Name"] = requested_profile
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/signals/batch_update",
            headers=headers,
            json={"signals": [{"signal_name": "VehicleSpeed", "value": 80.0}]},
        )

    assert response.status_code == 202
    body = response.json()
    assert body["queued"] == []
    assert body["count"] == 0
    assert body["warnings"][0]["code"] == warning_code
    app.state.writer.send_signals_batch.assert_not_awaited()
    assert app.state.writer.writes == []
