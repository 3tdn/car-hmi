"""Kịch bản test: quản lý profile + phân quyền theo signal.

Bao phủ luồng: tạo/cập nhật profile, chuyển active profile (global & theo
client), rồi xác nhận quyền đọc/ghi/subscribe signal thay đổi tương ứng khi
signal có/không nằm trong profile.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


@pytest.mark.asyncio
async def test_create_profile_then_update_adds_write_permission(app_builder, monkeypatch, tmp_path):
    """Admin (full) tạo profile 'driver' chỉ có 'read'; ghi bị chặn cho tới khi admin update thêm 'write'."""
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
    """Chuyển active profile cần quyền 'full'; sau khi chuyển, scope đọc/ghi đổi theo profile mới."""
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
        # viewer thiếu quyền 'full' nên không đổi được active profile.
        denied = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"},
            json={"name": "operator"},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["required_permission"] == "full"

        # Khi active vẫn là viewer: đọc VehicleSpeed OK, FuelLevel bị chặn.
        vs_ok = await c.get("/signals/VehicleSpeed", headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"})
        assert vs_ok.status_code == 200
        fl_denied = await c.get("/signals/FuelLevel", headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer"})
        assert fl_denied.status_code == 403

        # Dev Mode header cho phép bỏ qua yêu cầu quyền 'full' để đổi profile.
        switched = await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "viewer", "X-Dev-Mode": "true"},
            json={"name": "operator"},
        )
        assert switched.status_code == 200
        assert switched.json()["active"] == "operator"

        # Sau khi chuyển sang operator: FuelLevel đọc/ghi được, VehicleSpeed bị chặn.
        fl_ok = await c.get("/signals/FuelLevel", headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"})
        assert fl_ok.status_code == 200
        fl_write = await c.put(
            "/signals/FuelLevel",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 33.0},
        )
        assert fl_write.status_code == 202
        vs_denied_now = await c.get("/signals/VehicleSpeed", headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"})
        assert vs_denied_now.status_code == 403
        assert writer.writes == [("FuelLevel", 33.0)]


@pytest.mark.asyncio
async def test_per_client_active_profile_session_is_isolated(app_builder, monkeypatch, tmp_path):
    """Mỗi client (X-Client-Id) có thể chọn active profile khác nhau độc lập với global active."""
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

        # Client khác không có X-Client-Id vẫn thấy global active = admin.
        other_client = await c.get("/api/profiles", headers={"X-API-Key": "test-key"})
        assert other_client.json()["active"] == "admin"

        # Client tablet-1 vẫn thấy active = viewer (write bị chặn vì viewer chỉ có 'read').
        tablet_write = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Client-Id": "tablet-1"},
            json={"value": 99.0},
        )
        assert tablet_write.status_code == 403
        assert writer.writes == []


@pytest.mark.asyncio
async def test_signal_outside_any_profile_scope_is_denied_for_read_write_and_batch(app_builder, monkeypatch, tmp_path):
    """Signal không khai báo trong profile bị chặn ở mọi kênh: read, write, batch."""
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
        initial_signals={"VehicleSpeed": 10.0},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        read_denied = await c.get(
            "/signals/CoolantTemp", headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"}
        )
        assert read_denied.status_code == 403

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


def test_ws_subscribe_scope_follows_active_profile_switch(app_builder_sync):
    """WS subscribe '*' phản ánh đúng signal scope của profile được truyền lúc connect."""
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
            assert ack_viewer["channels"] == ["VehicleSpeed"]

        with sc.websocket_connect("/ws/subscribe?profile_name=operator") as ws_operator:
            ws_operator.send_text(json.dumps({"type": "subscribe", "signals": ["*"]}))
            ack_operator = json.loads(ws_operator.receive_text())
            assert ack_operator["channels"] == ["FuelLevel"]
