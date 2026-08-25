"""Tests for Dev Mode endpoints and seat write locks."""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.devmode_locks import reset_seat_lock_registry
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []


class _FakeWriter:
    def __init__(self):
        self.writes: list[tuple[str, float]] = []

    async def send_signal(self, signal_name, value):
        self.writes.append((signal_name, value))

    async def send_signals_batch(self, values):
        for signal_name, value in values.items():
            self.writes.append((signal_name, value))
        return values, []


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_seat_lock_registry()
    yield
    reset_seat_lock_registry()


async def _build_app():
    store = SignalStore()
    now = time.time()
    for seat in ("FL", "FR", "RL1", "RL2", "RR1"):
        await store.update(f"COM_Status_Puma{seat}Can", 1.0, timestamp=now)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    return app


def _headers(client_id: str) -> dict[str, str]:
    return {"X-API-Key": "test-key", "X-Client-Id": client_id, "X-Dev-Mode": "true"}


@pytest.mark.asyncio
async def test_catalog_lists_four_signal_families():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/devmode/catalog", headers=_headers("c1"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["seats"] == ["fl", "fr", "rl1", "rl2", "rr1"]
    names = {family["signal_name"] for family in body["families"]}
    assert names == {"ACR_RetractRequest", "ABL_RetractRequest", "ISB_Color", "HB_Request"}


@pytest.mark.asyncio
async def test_select_seats_locks_out_other_sections():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True, "fr": False}, "block_timeout_sec": 60},
        )
        assert resp.status_code == 200
        assert resp.json()["applied"]["fl"]["selected"] is True

        blocked = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("intruder"),
            json={"seats": {"fl": True}},
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["applied"]["fl"]["error"] == "seat_locked"


@pytest.mark.asyncio
async def test_write_signal_blocked_for_other_section_until_exit():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}},
        )

        blocked = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=_headers("intruder"),
            json={"value": 5},
        )
        assert blocked.status_code == 423
        assert blocked.json()["detail"]["code"] == "devmode_seat_locked"

        allowed_other_seat = await c.put(
            "/signals/ACR_FR_RetractRequest",
            headers=_headers("intruder"),
            json={"value": 5},
        )
        assert allowed_other_seat.status_code == 202

        owner_write = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=_headers("owner"),
            json={"value": 5},
        )
        assert owner_write.status_code == 202

        await c.post("/api/devmode/exit", headers=_headers("owner"))
        after_exit = await c.put(
            "/signals/ACR_FL_RetractRequest",
            headers=_headers("intruder"),
            json={"value": 5},
        )
        assert after_exit.status_code == 202


@pytest.mark.asyncio
async def test_batch_write_skips_locked_seat_with_warning():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"rl1": True}},
        )
        resp = await c.post(
            "/signals/batch_update",
            headers=_headers("intruder"),
            json={
                "signals": [
                    {"signal_name": "ACR_RL1_RetractRequest", "value": 5},
                    {"signal_name": "ACR_RL2_RetractRequest", "value": 5},
                ]
            },
        )

    assert resp.status_code == 202
    body = resp.json()
    assert [item["signal_name"] for item in body["queued"]] == ["ACR_RL2_RetractRequest"]
    assert body["warnings"][0]["code"] == "devmode_seat_locked"


@pytest.mark.asyncio
async def test_apply_signal_fans_out_to_selected_seats():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "ABL_RetractRequest",
                "value": 3,
                "seats": {"fl": True, "fr": True, "rl1": False},
            },
        )

    assert resp.status_code == 200
    applied = resp.json()["applied"]
    assert set(applied) == {"fl", "fr"}
    assert app.state.writer.writes == [
        ("ABL_FL_RetractRequest", 3.0),
        ("ABL_FR_RetractRequest", 3.0),
    ]


@pytest.mark.asyncio
async def test_isb_color_is_split_into_rgb_signals():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "ISB_Color", "value": 65280, "seats": {"fl": True}},
        )

    assert resp.status_code == 200
    assert app.state.writer.writes == [
        ("ISB_FL_ColorRed", 0.0),
        ("ISB_FL_ColorGreen", 255.0),
        ("ISB_FL_ColorBlue", 0.0),
    ]


@pytest.mark.asyncio
async def test_apply_signal_rejects_unknown_family_and_value():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        unknown = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "Nope", "value": 1, "seats": {"fl": True}},
        )
        bad_value = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "HB_Request", "value": 9, "seats": {"fl": True}},
        )

    assert unknown.status_code == 422
    assert bad_value.status_code == 422


@pytest.mark.asyncio
async def test_seat_not_connected_is_rejected():
    store = SignalStore()
    await store.update("COM_Status_PumaFLCan", 0.0, timestamp=time.time())
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}},
        )

    assert resp.status_code == 409
    assert resp.json()["detail"]["applied"]["fl"]["error"] == "seat_not_connected"


@pytest.mark.asyncio
async def test_missing_or_stale_connectivity_is_rejected():
    store = SignalStore()
    await store.update("COM_Status_PumaFRCan", 1.0, timestamp=time.time() - 31)
    app = create_app(store, _FakeRepo(), api_key="test-key")
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        missing = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}},
        )
        stale = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fr": True}},
        )

    assert missing.status_code == 409
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_lock_operations_require_client_id():
    app = await _build_app()
    headers = {"X-API-Key": "test-key", "X-Dev-Mode": "true"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        select = await c.post(
            "/api/devmode/seats/select",
            headers=headers,
            json={"seats": {"fl": True}},
        )
        apply = await c.post(
            "/api/devmode/signals",
            headers=headers,
            json={"signal_name": "ABL_RetractRequest", "value": 1, "seats": {"fl": True}},
        )
        exit_response = await c.post("/api/devmode/exit", headers=headers)

    assert select.status_code == 400
    assert apply.status_code == 400
    assert exit_response.status_code == 400


@pytest.mark.asyncio
async def test_timeout_must_be_between_one_second_and_one_hour():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        negative = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}, "block_timeout_sec": -5},
        )
        excessive = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}, "block_timeout_sec": 3601},
        )

    assert negative.status_code == 422
    assert excessive.status_code == 422


@pytest.mark.asyncio
async def test_devmode_cleanup_task_is_lazy_and_stops_when_no_lock():
    app = await _build_app()
    assert app.state.profile_session_cleanup_task is None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        select_resp = await c.post(
            "/api/devmode/seats/select",
            headers=_headers("owner"),
            json={"seats": {"fl": True}, "block_timeout_sec": 60},
        )
        assert select_resp.status_code == 200
        task = app.state.profile_session_cleanup_task
        assert task is not None
        assert not task.done()

        exit_resp = await c.post("/api/devmode/exit", headers=_headers("owner"))
        assert exit_resp.status_code == 200

    assert app.state.profile_session_cleanup_task is None
