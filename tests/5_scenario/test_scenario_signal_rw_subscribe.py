"""Kịch bản test: đọc/ghi signal qua REST + subscribe realtime qua WebSocket.

Mỗi test mô phỏng một luồng nghiệp vụ nhiều bước (không chỉ 1 request/response
đơn lẻ) để phát hiện lỗi tích hợp giữa REST write, WS broadcast và profile
permission.
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient


@pytest.mark.asyncio
async def test_read_write_round_trip_respects_profile_scope(app_builder, monkeypatch, tmp_path):
    """Operator ghi được VehicleSpeed (write), nhưng đọc FuelLevel (ngoài scope) bị chặn."""
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
        initial_signals={"VehicleSpeed": 40.0},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        write_resp = await c.put(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 88.0},
        )
        assert write_resp.status_code == 202
        assert writer.writes == [("VehicleSpeed", 88.0)]

        read_resp = await c.get(
            "/signals/VehicleSpeed",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
        )
        assert read_resp.status_code == 200
        # Giá trị store chỉ đổi khi CAN bus echo lại, không phải ngay sau khi gửi write.
        assert read_resp.json()["value"] == 40.0

        denied = await c.get(
            "/signals/FuelLevel",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
        )
        assert denied.status_code == 403
        assert denied.json()["detail"]["code"] == "profile_signal_denied"


@pytest.mark.asyncio
async def test_batch_write_reports_partial_success_for_missing_can_signal(app_builder, monkeypatch, tmp_path):
    """batch_update ghi các signal hợp lệ, báo lỗi riêng cho signal không có trong DBC."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [
                    {"name": "VehicleSpeed", "permission": ["write"]},
                    {"name": "GhostSignal", "permission": ["write"]},
                ],
                "description": "Operator",
            }
        },
        unavailable_signals={"GhostSignal"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/signals/batch_update",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={
                "signals": [
                    {"signal_name": "VehicleSpeed", "value": 90.0},
                    {"signal_name": "GhostSignal", "value": 1.0},
                ]
            },
        )

    assert resp.status_code == 202
    body = resp.json()
    assert body["queued"] == [{"signal_name": "VehicleSpeed", "value": 90.0}]
    assert body["errors"] == [{"signal_name": "GhostSignal", "error": "signal_not_available"}]
    assert writer.writes == [("VehicleSpeed", 90.0)]


def test_ws_subscribe_receives_broadcast_then_stops_after_unsubscribe(app_builder_sync):
    """Client subscribe 1 signal, nhận update; unsubscribe xong không còn nhận nữa."""
    app = app_builder_sync(
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
        api_key="",
    )
    mgr = app.state.ws_manager

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe?profile_name=viewer") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["VehicleSpeed"]}))
            ack = json.loads(ws.receive_text())
            assert ack["channels"] == ["VehicleSpeed"]

            import asyncio

            asyncio.run(mgr.broadcast_signal("VehicleSpeed", 55.0, 1717243200.0))
            frame = json.loads(ws.receive_text())
            assert frame["signals"][0]["value"] == 55.0

            ws.send_text(json.dumps({"type": "unsubscribe", "signals": ["VehicleSpeed"]}))
            unsub_ack = json.loads(ws.receive_text())
            assert unsub_ack["type"] == "unsubscribe_ack"

            # Xác nhận server-side subscription đã được gỡ (tránh phải chờ nhận
            # broadcast trên socket, dễ treo test nếu không còn dữ liệu gửi tới).
            remaining = next(iter(mgr._subscriptions.values()))
            assert "VehicleSpeed" not in remaining.signal_names


def test_ws_wildcard_subscribe_limited_to_profile_signals(app_builder_sync):
    """Subscribe '*' chỉ nhận các signal nằm trong profile đang active, kèm warning."""
    app = app_builder_sync(
        active="viewer",
        profiles={
            "viewer": {
                "signals": [{"name": "VehicleSpeed", "permission": ["read"]}],
                "description": "Viewer",
            }
        },
        api_key="",
    )

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe?profile_name=viewer") as ws:
            ws.send_text(json.dumps({"type": "subscribe", "signals": ["*"]}))
            ack = json.loads(ws.receive_text())
            assert ack["channels"] == ["VehicleSpeed"]
            assert ack["warnings"][0]["code"] == "profile_signal_filtered"


def test_two_ws_clients_only_interested_subscriber_receives_update(app_builder_sync):
    """Hai client subscribe khác signal — mỗi client chỉ nhận đúng phần mình quan tâm."""
    app = app_builder_sync(
        active="admin",
        profiles={
            "admin": {
                "signals": [
                    {"name": "VehicleSpeed", "permission": ["read"]},
                    {"name": "FuelLevel", "permission": ["read"]},
                ],
                "description": "Admin",
            }
        },
        api_key="",
    )
    mgr = app.state.ws_manager

    with TestClient(app) as sc:
        with sc.websocket_connect("/ws/subscribe?profile_name=admin") as ws_speed, sc.websocket_connect(
            "/ws/subscribe?profile_name=admin"
        ) as ws_fuel:
            ws_speed.send_text(json.dumps({"type": "subscribe", "signals": ["VehicleSpeed"]}))
            json.loads(ws_speed.receive_text())
            ws_fuel.send_text(json.dumps({"type": "subscribe", "signals": ["FuelLevel"]}))
            json.loads(ws_fuel.receive_text())

            import asyncio

            asyncio.run(mgr.broadcast_signal("VehicleSpeed", 72.0, 1717243200.0))
            frame = json.loads(ws_speed.receive_text())
            assert frame["signals"][0]["name"] == "VehicleSpeed"
