"""Kịch bản test bổ sung: các luồng biên chưa được các nhóm test khác che phủ.

Gồm: khóa ghế Dev Mode tự hết hạn theo thời gian, phiên client offline giải
phóng khóa ngay lập tức, xóa profile đang active tự chuyển sang profile khác
và dọn session, heartbeat/offline ảnh hưởng tới danh sách phiên.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient


def _connected_seats(*seats: str) -> dict[str, float]:
    return {f"COM_Status_Puma{seat.upper()}Can": 1.0 for seat in seats}


@pytest.mark.asyncio
async def test_seat_lock_expires_after_timeout_without_explicit_exit(app_builder, monkeypatch, tmp_path):
    """Khóa ghế Dev Mode tự hết hạn sau `block_timeout_sec`, không cần gọi /exit."""
    app, _writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl"),
    )

    headers_owner = {"X-API-Key": "test-key", "X-Client-Id": "owner", "X-Dev-Mode": "true"}
    headers_intruder = {"X-API-Key": "test-key", "X-Client-Id": "intruder", "X-Dev-Mode": "true"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/devmode/seats/select",
            headers=headers_owner,
            json={"seats": {"fl": True}, "block_timeout_sec": 1},
        )

        still_locked = await c.put(
            "/signals/ACR_FL_RetractRequest", headers=headers_intruder, json={"value": 5}
        )
        assert still_locked.status_code == 423

        await asyncio.sleep(1.2)

        after_expiry = await c.put(
            "/signals/ACR_FL_RetractRequest", headers=headers_intruder, json={"value": 5}
        )
        assert after_expiry.status_code == 202


@pytest.mark.asyncio
async def test_marking_client_session_offline_releases_its_devmode_locks(app_builder, monkeypatch, tmp_path):
    """`POST /api/profile/offline` giải phóng ngay các khóa ghế Dev Mode của client đó."""
    app, _writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl"),
    )

    owner_headers = {"X-API-Key": "test-key", "X-Client-Id": "tablet-1", "X-Dev-Mode": "true"}
    intruder_headers = {"X-API-Key": "test-key", "X-Client-Id": "tablet-2", "X-Dev-Mode": "true"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/devmode/seats/select",
            headers=owner_headers,
            json={"seats": {"fl": True}, "block_timeout_sec": 60},
        )

        blocked = await c.put(
            "/signals/ACR_FL_RetractRequest", headers=intruder_headers, json={"value": 5}
        )
        assert blocked.status_code == 423

        offline_resp = await c.post("/api/profile/offline", headers={"X-API-Key": "test-key", "X-Client-Id": "tablet-1"})
        assert offline_resp.status_code == 200

        released_write = await c.put(
            "/signals/ACR_FL_RetractRequest", headers=intruder_headers, json={"value": 5}
        )
        assert released_write.status_code == 202


@pytest.mark.asyncio
async def test_deleting_active_profile_promotes_next_profile_and_clears_its_sessions(app_builder, monkeypatch, tmp_path):
    """Xóa profile đang active: server tự chuyển active sang profile còn lại và dọn session trỏ tới nó."""
    app, _writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={
            "admin": {"signals": [{"name": "VehicleSpeed", "permission": ["full"]}], "description": "Admin"},
            "viewer": {"signals": [{"name": "VehicleSpeed", "permission": ["read"]}], "description": "Viewer"},
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Client "phone-1" tự chọn active = viewer.
        await c.put(
            "/api/profile/active",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "admin", "X-Client-Id": "phone-1"},
            json={"name": "viewer"},
        )

        delete_resp = await c.delete(
            "/api/profile/viewer", headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"}
        )
        assert delete_resp.status_code == 204

        # Session của phone-1 trỏ tới profile đã xóa phải được dọn — client rơi về global active.
        list_resp = await c.get(
            "/api/profiles", headers={"X-API-Key": "test-key", "X-Client-Id": "phone-1"}
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["active"] == "admin"

        profiles_resp = await c.get("/api/profiles", headers={"X-API-Key": "test-key"})
        names = {p["name"] for p in profiles_resp.json()["profiles"]}
        assert names == {"admin"}


@pytest.mark.asyncio
async def test_heartbeat_and_offline_toggle_client_session_status_listing(app_builder, monkeypatch, tmp_path):
    """Heartbeat đánh dấu online; `/api/profile/offline` đánh dấu offline ngay trong danh sách session."""
    app, _writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [{"name": "VehicleSpeed", "permission": ["full"]}], "description": "Admin"}},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        heartbeat = await c.post(
            "/api/profile/heartbeat", headers={"X-API-Key": "test-key", "X-Client-Id": "kiosk-1"}
        )
        assert heartbeat.status_code == 200

        sessions_resp = await c.get(
            "/api/profile/sessions", headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"}
        )
        sessions = sessions_resp.json()["sessions"]
        kiosk_session = next(s for s in sessions if s["client_id"] == "kiosk-1")
        assert kiosk_session["status"] == "online"

        await c.post("/api/profile/offline", headers={"X-API-Key": "test-key", "X-Client-Id": "kiosk-1"})

        sessions_resp_after = await c.get(
            "/api/profile/sessions", headers={"X-API-Key": "test-key", "X-Profile-Name": "admin"}
        )
        sessions_after = sessions_resp_after.json()["sessions"]
        kiosk_after = next(s for s in sessions_after if s["client_id"] == "kiosk-1")
        assert kiosk_after["status"] == "offline"
