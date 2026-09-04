"""Scenario tests: Dev Mode restraint control by seat + related update APIs.

Covers: selecting seats, applying one signal-family value to multiple seats,
seat-based write locking between sections, disconnected seats / signals missing
from the DBC, and shared update APIs (ELK reset, SEAL airbag) outside Dev Mode.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _connected_seats(*seats: str) -> dict[str, float]:
    return {f"COM_Status_Puma{seat.upper()}Can": 1.0 for seat in seats}


def _headers(client_id: str, *, dev_mode: bool = True) -> dict[str, str]:
    headers = {"X-API-Key": "test-key", "X-Client-Id": client_id}
    if dev_mode:
        headers["X-Dev-Mode"] = "true"
    return headers


@pytest.mark.asyncio
async def test_select_seats_then_apply_signal_family_writes_per_seat_can_signals(app_builder, monkeypatch, tmp_path):
    """Select 2 seats, then apply ACR_RetractRequest — each seat receives the correct expanded CAN signal."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl", "fr"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        select_resp = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True, "fr": True}, "block_timeout_sec": 60},
        )
        assert select_resp.status_code == 200
        assert select_resp.json()["applied"]["fl"]["selected"] is True

        apply_resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "ACR_RetractRequest",
                "value": 10,
                "seats": {"fl": True, "fr": True},
            },
        )
        assert apply_resp.status_code == 200
        applied = apply_resp.json()["applied"]
        assert applied["fl"]["signal_name"] == "ACR_RetractRequest"
        assert applied["fr"]["signal_name"] == "ACR_RetractRequest"

    assert ("ACR_FL_RetractRequest", 10.0) in writer.writes
    assert ("ACR_FR_RetractRequest", 10.0) in writer.writes


@pytest.mark.asyncio
async def test_apply_signal_skips_disconnected_seat_but_applies_others(app_builder, monkeypatch, tmp_path):
    """A seat without COM status (disconnected) is marked as an error, while the other seat is still applied."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl"),  # rl1 intentionally has no COM status
    )
    app.state.devmode_bypass_can_status = False

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "HB_Request",
                "value": 1,
                "seats": {"fl": True, "rl1": True},
            },
        )

    assert resp.status_code == 200
    applied = resp.json()["applied"]
    assert applied["fl"]["signal_name"] == "HB_Request"
    assert applied["rl1"]["error"] == "seat_not_connected"
    assert ("HB_Request_FL", 1.0) in writer.writes
    assert ("HB_Request_RL1", 1.0) not in writer.writes


@pytest.mark.asyncio
async def test_apply_signal_reports_signal_not_available_for_missing_can_signal(app_builder, monkeypatch, tmp_path):
    """A seat missing the corresponding signal in the DBC returns `signal_not_available`."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl", "rr1"),
        unavailable_signals={"HB_Request_RR1"},
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "HB_Request",
                "value": 2,
                "seats": {"fl": True, "rr1": True},
            },
        )

    assert resp.status_code == 200
    applied = resp.json()["applied"]
    assert applied["fl"]["signal_name"] == "HB_Request"
    assert applied["rr1"]["error"] == "signal_not_available"
    assert ("HB_Request_FL", 2.0) in writer.writes


@pytest.mark.asyncio
async def test_apply_signal_allows_value_outside_allowed_values(app_builder, monkeypatch, tmp_path):
    """Dev Mode does not limit values by the signal family's allowed_values list."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "HB_Request", "value": 99, "seats": {"fl": True}},
        )

    assert resp.status_code == 200
    assert writer.writes == [("HB_Request_FL", 99.0)]


@pytest.mark.asyncio
async def test_seat_lock_blocks_other_section_until_exit_then_release(app_builder, monkeypatch, tmp_path):
    """A different section cannot select/write a locked seat until the owner exits Dev Mode."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}},
        )

        intruder_select = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("intruder"),
            json={"seats": {"fl": True}},
        )
        assert intruder_select.status_code == 409

        intruder_write = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=_headers("intruder"),
            json={"value": 5},
        )
        assert intruder_write.status_code == 423
        assert intruder_write.json()["detail"]["code"] == "devmode_seat_locked"

        owner_write = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=_headers("owner"),
            json={"value": 5},
        )
        assert owner_write.status_code == 202

        await c.post("/api/devmode/exit", headers=_headers("owner"))

        intruder_write_after_exit = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=_headers("intruder"),
            json={"value": 5},
        )
        assert intruder_write_after_exit.status_code == 202

    assert ("ACR_FL_RetractRequest", 5.0) in writer.writes


@pytest.mark.asyncio
async def test_batch_update_skips_locked_seat_with_warning_but_allows_owner(app_builder, monkeypatch, tmp_path):
    """`/signals/batch_update` skips signals on Dev Mode-locked seats and warns, while the owner can still write."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("rl1", "rl2"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"rl1": True}},
        )

        intruder_resp = await c.post(
            "/signals/batch_update",
            headers=_headers("intruder"),
            json={
                "signals": [
                    {"signal_name": "ACR_RL1_RetractRequest", "value": 5},
                    {"signal_name": "ACR_RL2_RetractRequest", "value": 5},
                ]
            },
        )
        assert intruder_resp.status_code == 202
        body = intruder_resp.json()
        assert body["queued"] == [{"signal_name": "ACR_RL2_RetractRequest", "value": 5.0}]
        assert body["warnings"][0]["code"] == "devmode_seat_locked"

        owner_resp = await c.post(
            "/signals/batch_update",
            headers=_headers("owner"),
            json={"signals": [{"signal_name": "ACR_RL1_RetractRequest", "value": 5}]},
        )
        assert owner_resp.status_code == 202
        assert owner_resp.json()["queued"] == [{"signal_name": "ACR_RL1_RetractRequest", "value": 5.0}]


@pytest.mark.asyncio
async def test_devmode_catalog_and_status_reflect_selection(app_builder, monkeypatch, tmp_path):
    """`GET /api/devmode/catalog` lists 4 signal families; `GET /api/devmode/status` reflects the selected seats."""
    app, _writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="admin",
        profiles={"admin": {"signals": [], "description": "Admin"}},
        initial_signals=_connected_seats("fl", "fr", "rl1", "rl2", "rr1"),
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        catalog = await c.get("/api/devmode/catalog", headers=_headers("owner"))
        assert catalog.status_code == 200
        family_names = {f["signal_name"] for f in catalog.json()["families"]}
        assert family_names == {"ACR_RetractRequest", "ABL_RetractRequest", "ISB_Color", "HB_Request"}

        await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"rr1": True}, "block_timeout_sec": 30},
        )
        status_resp = await c.get("/api/devmode/status", headers=_headers("owner"))
        assert status_resp.status_code == 200
        seats_status = status_resp.json()["seats"]
        assert seats_status["rr1"]["selected"] is True
        assert seats_status["rr1"]["owned"] is True
        assert seats_status["fl"]["selected"] is False


@pytest.mark.asyncio
async def test_elk_reset_and_seal_airbag_writes_use_normal_signal_permission(app_builder, monkeypatch, tmp_path):
    """The ELK reset flag / SEAL airbag update APIs use the shared signal-write endpoint and still follow profile permissions."""
    app, writer = await app_builder(
        monkeypatch,
        tmp_path,
        active="operator",
        profiles={
            "operator": {
                "signals": [
                    {"name": "ELK_ResetErrorFlags", "permission": ["write"]},
                    {"name": "SEAL_InflateAirbag", "permission": ["read"]},
                ],
                "description": "Operator",
            }
        },
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        reset_resp = await c.put(
            "/signals/ELK_ResetErrorFlags",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 1},
        )
        assert reset_resp.status_code == 202

        airbag_resp = await c.put(
            "/signals/SEAL_InflateAirbag",
            headers={"X-API-Key": "test-key", "X-Profile-Name": "operator"},
            json={"value": 1},
        )
        assert airbag_resp.status_code == 403
        assert airbag_resp.json()["detail"]["code"] == "profile_signal_denied"

    assert writer.writes == [("ELK_ResetErrorFlags", 1.0)]
