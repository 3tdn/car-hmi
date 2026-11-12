"""Tests for Dev Mode endpoints and seat write locks."""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.can_io.parser import DatabaseLoader
from src.core.devmode_locks import get_seat_lock_registry, reset_seat_lock_registry
from src.core.signal_store import SignalStore


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
    app = create_app(store, api_key="test-key")
    app.state.writer = _FakeWriter()
    return app


def _headers(client_id: str) -> dict[str, str]:
    return {"X-API-Key": "test-key", "X-Client-Id": client_id, "X-Dev-Mode": "true"}


@pytest.mark.asyncio
async def test_catalog_lists_five_signal_families():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/devmode/catalog", headers=_headers("c1"))

    assert resp.status_code == 200
    body = resp.json()
    assert body["seats"] == ["fl", "fr", "rl1", "rl2", "rr1"]
    names = {family["signal_name"] for family in body["families"]}
    assert names == {
        "ACR_RetractRequest",
        "ABL_RetractRequest",
        "ELK_LockingRequest",
        "ISB_Color",
        "HB_Request",
    }


@pytest.mark.asyncio
async def test_isb_catalog_has_no_presets_or_intensity_dbc_states(monkeypatch):
    app = await _build_app()
    monkeypatch.setattr(
        app.state.signal_metadata,
        "get",
        lambda name: {"states": [{"value": 100, "description": "Full intensity"}]}
        if name == "ISB_FL_Intensity" else None,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/devmode/catalog", headers=_headers("c1"))

    assert resp.status_code == 200
    family = next(f for f in resp.json()["families"] if f["signal_name"] == "ISB_Color")
    assert family["states"] == []


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
@pytest.mark.parametrize(
    ("value", "intensity", "rgb"),
    [
        (0x000000, 0, (0, 0, 0)),
        (0xFFFFFF, 100, (255, 255, 255)),
        (0xFF0000, 100, (255, 0, 0)),
        (0x00FF00, 100, (0, 255, 0)),
        (0x0000FF, 100, (0, 0, 255)),
        (0x808080, 50, (128, 128, 128)),
        (0x804020, 50, (128, 64, 32)),
        (0x0A141E, 12, (10, 20, 30)),
        (0x007F00, 50, (0, 127, 0)),
        (0x000001, 0, (0, 0, 1)),
        (0x070000, 3, (7, 0, 0)),
        (0x9333EA, 92, (147, 51, 234)),
    ],
)
async def test_isb_color_derives_intensity_and_splits_rgb_for_all_selected_seats(value, intensity, rgb):
    app = await _build_app()
    seats = dict.fromkeys(("fl", "fr", "rl1", "rl2", "rr1"), True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "ISB_Color",
                "value": value,
                "seats": seats,
            },
        )

    assert resp.status_code == 200
    expected_writes = {}
    for seat in seats:
        expected = {
            f"ISB_{seat.upper()}_Intensity": float(intensity),
            f"ISB_{seat.upper()}_ColorRed": float(rgb[0]),
            f"ISB_{seat.upper()}_ColorGreen": float(rgb[1]),
            f"ISB_{seat.upper()}_ColorBlue": float(rgb[2]),
        }
        assert resp.json()["applied"][seat]["signals"] == expected
        assert resp.json()["applied"][seat]["value"] == value
        expected_writes.update(expected)
    assert app.state.writer.writes == list(expected_writes.items())


@pytest.mark.asyncio
@pytest.mark.parametrize("include_fl", [True, False])
@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(0, 127), (0, 200), (10, 210), (None, None), (0, 0)],
)
async def test_isb_color_intensity_is_independent_of_metadata(minimum, maximum, include_fl):
    app = await _build_app()
    loader = DatabaseLoader()
    loader.load_dbc("db/can_db/Interface_Panther_To_CarPC_v12.dbc")
    # RGB (7,0,0) always gives round(7 * 100 / 255) = 3, regardless of DBC bounds.
    bounds = {
        "fl": (minimum, maximum),
        "fr": (0, 100),
        "rl1": (10, 210),
        "rl2": (None, None),
        "rr1": (0, 0),
    }
    for seat, (seat_minimum, seat_maximum) in bounds.items():
        name = f"ISB_{seat.upper()}_Intensity"
        signal = loader.get_message_for_signal(name).signals[name]
        signal.minimum = seat_minimum
        signal.maximum = seat_maximum
    app.state.signal_metadata.replace_from_loaders([loader])
    selected = {seat: True for seat in bounds if include_fl or seat != "fl"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "ISB_Color",
                "value": 0x070000,
                "seats": selected,
            },
        )

    assert resp.status_code == 200
    assert set(resp.json()["applied"]) == set(selected)
    for seat in selected:
        expected = {
            f"ISB_{seat.upper()}_Intensity": 3,
            f"ISB_{seat.upper()}_ColorRed": 7,
            f"ISB_{seat.upper()}_ColorGreen": 0,
            f"ISB_{seat.upper()}_ColorBlue": 0,
        }
        assert resp.json()["applied"][seat]["signals"] == expected
        for item in expected.items():
            assert item in app.state.writer.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [-1, 0x1000000, 0x64FF0000, 0x65FF0000, 1.5])
async def test_isb_color_rejects_invalid_rgb_without_writing_or_locking(value):
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "ISB_Color",
                "value": value,
                "seats": {"fl": True},
            },
        )

    assert resp.status_code == 422
    assert resp.json()["detail"] == (
        "ISB_Color must be an integer 0xRRGGBB value between 0x000000 and 0xFFFFFF"
    )
    assert app.state.writer.writes == []
    assert get_seat_lock_registry().active_locks() == {}


@pytest.mark.asyncio
async def test_elk_locking_request_expands_to_selected_seats():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={
                "signal_name": "ELK_LockingRequest",
                "value": 1,
                "seats": {"fl": True, "rr1": True},
            },
        )

    assert resp.status_code == 200
    assert app.state.writer.writes == [
        ("ELK_FL_LockingRequest", 1.0),
        ("ELK_RR1_LockingRequest", 1.0),
    ]


@pytest.mark.asyncio
async def test_apply_signal_rejects_unknown_family_but_allows_unlisted_value():
    app = await _build_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        unknown = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "Nope", "value": 1, "seats": {"fl": True}},
        )
        unlisted_value = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "HB_Request", "value": 9, "seats": {"fl": True}},
        )

    assert unknown.status_code == 422
    assert unlisted_value.status_code == 200
    assert app.state.writer.writes == [("HB_FL_Request", 9.0)]


@pytest.mark.asyncio
async def test_seat_not_connected_is_rejected():
    store = SignalStore()
    await store.update("COM_Status_PumaFLCan", 0.0, timestamp=time.time())
    app = create_app(store, api_key="test-key")
    app.state.devmode_bypass_can_status = False
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
    app = create_app(store, api_key="test-key")
    app.state.devmode_bypass_can_status = False
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
async def test_bypass_can_status_allows_devmode_signal_write():
    store = SignalStore()
    app = create_app(store, api_key="test-key")
    app.state.devmode_bypass_can_status = True
    app.state.writer = _FakeWriter()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/devmode/signals",
            headers=_headers("owner"),
            json={"signal_name": "ABL_RetractRequest", "value": 1, "seats": {"fl": True}},
        )

    assert resp.status_code == 200
    assert resp.json()["applied"]["fl"]["value"] == 1
    assert app.state.writer.writes == [("ABL_FL_RetractRequest", 1.0)]


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
