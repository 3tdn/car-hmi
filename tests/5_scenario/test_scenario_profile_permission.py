"""Scenario tests: profile management + signal permissions.

Covers the flow of creating/updating profiles, switching the active profile
(globally and per client), then confirming that signal read/write/subscribe
TX permissions change with the profile while RX remains unrestricted.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


@pytest.mark.asyncio
@pytest.mark.parametrize("permissions", [None, ["read"], ["write"], ["full"]])
async def test_rx_reads_do_not_require_profile_entries(app_builder, monkeypatch, tmp_path, permissions):
    """RX-only values, metadata and history stay readable even for an empty profile."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="operator",
        profiles={"operator": {"signals": (
            [{"name": "VehicleSpeed", "permission": permissions}] if permissions else []
        )}},
        initial_signals={"VehicleSpeed": 10.0, "OMS_State_Camera": 1.0},
    )

    async def query_history(**kwargs):
        assert kwargs["signal_name"] == "OMS_State_Camera"
        return [SimpleNamespace(signal_name="OMS_State_Camera", value=1.0, unit=None, timestamp=123.0)]

    monkeypatch.setattr(app.state.repo, "query_signals", query_history)
    headers = {"X-API-Key": "test-key", "X-Profile-Name": "operator"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        value = await c.get("/signals/OMS_State_Camera", headers=headers)
        assert value.status_code == 200
        assert value.json()["value"] == 1.0

        snapshot = await c.get("/signals", headers=headers)
        assert snapshot.status_code == 200
        assert {item["signal_name"] for item in snapshot.json()["items"]} == {"VehicleSpeed", "OMS_State_Camera"}
        assert snapshot.json()["warnings"] == []

        metadata = await c.get("/signals/available", headers=headers)
        assert metadata.status_code == 200
        rx = next(item for item in metadata.json()["signals_info"] if item["signal_name"] == "OMS_State_Camera")
        assert rx["writable"] is False
        assert rx["value"] == 1.0
        assert rx["timestamp"] is not None
        assert metadata.json()["warnings"] == []

        history = await c.get("/signals/OMS_State_Camera/history", headers=headers)
        assert history.status_code == 200
        assert history.json()["items"][0]["value"] == 1.0

        denied = await c.put("/signals/OMS_State_Camera", headers=headers, json={"value": 0.0})
        assert denied.status_code == 403
        assert writer.writes == []


@pytest.mark.parametrize("endpoint", ["/ws/signals", "/ws/subscribe"])
@pytest.mark.parametrize("signals", [[], [{"name": "VehicleSpeed", "permission": ["write"]}]])
@pytest.mark.parametrize("channels", [["*"], ["OMS_State_Camera"]])
def test_rx_subscription_delivers_without_read_permission(app_builder_sync, endpoint, signals, channels):
    """Both WS endpoints deliver RX outside empty or write-only profiles."""
    app = app_builder_sync(active="operator", profiles={"operator": {"signals": signals}}, api_key="test-key")
    with TestClient(app) as sc, sc.websocket_connect(
        f"{endpoint}?api_key=test-key&profile_name=operator"
    ) as ws:
        ws.send_json({"type": "subscribe", "signals": channels})
        ack = ws.receive_json()
        assert ack["channels"] == channels
        assert ack["warnings"] == []
        sc.portal.call(app.state.ws_manager.broadcast_signal, "OMS_State_Camera", 1.0, 123.0)
        frame = ws.receive_json()
        assert frame["signals"] == [{"name": "OMS_State_Camera", "std_name": "OMS_State_Camera", "value": 1.0}]


@pytest.mark.asyncio
async def test_create_profile_then_update_adds_write_permission(app_builder, monkeypatch, tmp_path):
    """Admin (full) creates profile 'driver' with only 'read'; writes are blocked until admin updates it to add 'write'."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            }
        },
        initial_signals={"VehicleSpeed": 10.0},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        create_resp = await c.post(
            "/api/profile",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
            json={
                "name": "driver",
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Driver",
            },
        )
        assert create_resp.status_code == 201
        section_id = create_resp.json()["section_id"]

        denied_write = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "driver"},
            json={"value": 50.0},
        )
        assert denied_write.status_code == 403
        assert denied_write.json()["detail"]["code"] == "profile_permission_denied"

        update_resp = await c.put(
            "/api/profile",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"},
            json={
                "name": "driver",
                "section_id": section_id,
                "signals": [{"name": "VehicleSpeed", "permission": ["read", "write"]}],
                "description": "Driver",
            },
        )
        assert update_resp.status_code == 200

        allowed_write = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "driver"},
            json={"value": 50.0},
        )
        assert allowed_write.status_code == 202
        assert writer.writes == [("VehicleSpeed", 50.0)]


@pytest.mark.asyncio
async def test_switch_active_profile_requires_full_permission_and_changes_scope(app_builder, monkeypatch, tmp_path):
    """Switching the active profile requires 'full'; after switching, only the write scope changes with the new profile."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
            "operator": {
                "signals": [{"name": "FuelLevel", "permission": ["read", "write"]}],
                "description": "Operator",
            },
        },
        initial_signals={"VehicleSpeed": 10.0, "FuelLevel": 55.0},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # viewer lacks 'full', so it cannot change the active profile.
        denied = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"},
            json={"name": "operator"},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["required_permission"] == "full"

        # Both signals are readable even though FuelLevel is outside the viewer profile.
        vs_ok = await c.get("/signals/VehicleSpeed", headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"})
        assert vs_ok.status_code == 200
        fl_read = await c.get("/signals/FuelLevel", headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"})
        assert fl_read.status_code == 200

        # The Dev Mode header allows bypassing the 'full' permission requirement to switch profiles.
        switched = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer", "X-Dev-Mode": "true"},
            json={"name": "operator"},
        )
        assert switched.status_code == 200
        assert switched.json()["active"] == "operator"

        # After switching, FuelLevel can be written and both signals remain readable.
        fl_ok = await c.get("/signals/FuelLevel", headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"})
        assert fl_ok.status_code == 200
        fl_write = await c.put(
            "/signals/FuelLevel",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 33.0},
        )
        assert fl_write.status_code == 202
        vs_read = await c.get("/signals/VehicleSpeed", headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"})
        assert vs_read.status_code == 200
        vs_write_denied = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 44.0},
        )
        assert vs_write_denied.status_code == 403
        assert writer.writes == [("FuelLevel", 33.0)]


@pytest.mark.asyncio
async def test_per_client_active_profile_session_is_isolated(app_builder, monkeypatch, tmp_path):
    """Each client (X-Client-Id) can independently choose an active profile different from the global active profile."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={
            "admin": {
                "signals": [{"name": "VehicleSpeed", "permission": ["full"]}],
                "description": "Admin",
            },
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
        },
        initial_signals={"VehicleSpeed": 10.0},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin", "X-Client-Id": "tablet-1"},
            json={"name": "viewer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] == "viewer"
        assert body["global_active"] == "admin"

        # Another client without X-Client-Id still sees global active = admin.
        other_client = await c.get("/api/profiles", headers={"X-API-Key": "test-key"})
        assert other_client.json()["active"] == "admin"

        # Client tablet-1 still sees active = viewer (writes are blocked because viewer only has 'read').
        tablet_write = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Client-Id": "tablet-1"},
            json={"value": 99.0},
        )
        assert tablet_write.status_code == 403
        assert writer.writes == []


@pytest.mark.asyncio
async def test_signal_outside_profile_is_readable_but_denied_for_write_and_batch(app_builder, monkeypatch, tmp_path):
    """A signal outside the profile remains readable; single and batch TX stay restricted."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read", "write"]}],
                "description": "Operator",
            }
        },
        initial_signals={"VehicleSpeed": 10.0, "CoolantTemp": 80.0},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        read_allowed = await c.get(
            "/signals/CoolantTemp", headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"}
        )
        assert read_allowed.status_code == 200
        assert read_allowed.json()["value"] == 80.0

        write_denied = await c.put(
            "/signals/CoolantTemp",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 90.0},
        )
        assert write_denied.status_code == 403

        batch_resp = await c.post(
            "/signals/batch_update",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={
                "signals": [
                    {"signal_name": "VehicleSpeed", "value": 66.0},
                    {"signal_name": "CoolantTemp", "value": 90.0},
                ]
            },
        )
        assert batch_resp.status_code == 202
        body = batch_resp.json()
        assert body["queued"] == [{"signal_name": "VehicleSpeed", "value": 66.0}]
        assert body["warnings"][0]["code"] == "profile_signal_filtered"
        assert body["warnings"][0]["signals"] == ["CoolantTemp"]
        assert writer.writes == [("VehicleSpeed", 66.0)]


def test_ws_wildcard_is_unrestricted_across_profiles(app_builder_sync):
    """WS subscribe '*' reads all signals for either profile."""
    app = app_builder_sync(
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            },
            "operator": {
                "signals": [{"name": "FuelLevel", "permission": ["read"]}],
                "description": "Operator",
            },
        },
        api_key="",
    )

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe?profile_name=viewer") as ws_viewer:
            ws_viewer.send_text(json.dumps({"type": "subscribe", "signals": ["*"]}))
            ack_viewer = json.loads(ws_viewer.receive_text())
            assert ack_viewer["channels"] == ["*"]
            assert ack_viewer["warnings"] == []

        with sc.websocket_connect("/ws/subscribe?profile_name=operator") as ws_operator:
            ws_operator.send_text(json.dumps({"type": "subscribe", "signals": ["*"]}))
            ack_operator = json.loads(ws_operator.receive_text())
            assert ack_operator["channels"] == ["*"]
            assert ack_operator["warnings"] == []
