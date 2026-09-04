"""Functional tests for `GET /api/restraints/match` and `GET /api/restraints/video/{name}`.

This route had no existing tests (0% coverage) before this file was added.
`MEDIA_DIR`/store is monkeypatched so the tests do not depend on real files in
`media/` or a real CAN bus.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []


async def _build_client(monkeypatch, tmp_path, *, initial_signals=None, video_names=()):
    import src.api.routes.restraints as restraints_route

    monkeypatch.setattr(restraints_route, "MEDIA_DIR", tmp_path)
    for name in video_names:
        (tmp_path / name).write_bytes(b"fake-video-bytes")

    store = SignalStore()
    for name, value in (initial_signals or {}).items():
        await store.update(name, value)

    # api_key="" — the route has no auth dependency (see app.py), so leave it empty for brevity.
    app = create_app(store, _FakeRepo(), api_key="")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_match_rejects_invalid_crash_severity(monkeypatch, tmp_path):
    """crash_severity outside {35,40,50,56} is rejected with 422."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={"weight": 75, "height": 175, "crash_severity": 45, "seatbelt_system": "SLL"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_match_rejects_invalid_seatbelt_system(monkeypatch, tmp_path):
    """seatbelt_system outside {SLL,CLL,MSLL} is rejected with 422."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={"weight": 75, "height": 175, "crash_severity": 40, "seatbelt_system": "XYZ"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_match_rejects_invalid_seat(monkeypatch, tmp_path):
    """A seat other than 'fl'/'fr' is rejected with 422."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={"weight": 75, "height": 175, "crash_severity": 40, "seatbelt_system": "SLL", "seat": "rl1"},
        )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_match_returns_not_matched_when_media_dir_empty(monkeypatch, tmp_path):
    """When the media dir has no videos, matched=False and score=0."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={"weight": 75, "height": 175, "crash_severity": 40, "seatbelt_system": "SLL"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is False
    assert body["context"]["candidates_found"] == 0


@pytest.mark.asyncio
async def test_match_picks_best_scoring_video_among_candidates(monkeypatch, tmp_path):
    """A video matching percentile/velocity/seatbelt/zone exactly is chosen over a partially matching video."""
    async with await _build_client(
        monkeypatch,
        tmp_path,
        video_names=["50p_mid_40_SLL.mp4", "50p_front_40_SLL.mp4", "5p_mid_35_CLL.mp4"],
    ) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={
                "weight": 75,  # → 50th percentile
                "height": 175,
                "crash_severity": 40,
                "seatbelt_system": "SLL",
                "seat": "fl",
                "seat_x_mm": 100.0,  # → "mid" zone
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert body["video"]["filename"] == "50p_mid_40_SLL.mp4"
    assert body["context"]["seat_position_zone"] == "mid"
    assert body["context"]["seat_x_source"] == "hmi_param"
    assert body["context"]["derived_percentile"] == 50


@pytest.mark.asyncio
async def test_match_seat_x_zone_boundaries(monkeypatch, tmp_path):
    """seat_x_mm is split into the correct 3 zones: front (<56.75), mid, rear (>=170.25)."""
    async with await _build_client(
        monkeypatch, tmp_path, video_names=["50p_front_40_SLL.mp4", "50p_rear_40_SLL.mp4"]
    ) as c:
        front_resp = await c.get(
            "/api/restraints/match",
            params={
                "weight": 75, "height": 175, "crash_severity": 40,
                "seatbelt_system": "SLL", "seat_x_mm": 10.0,
            },
        )
        rear_resp = await c.get(
            "/api/restraints/match",
            params={
                "weight": 75, "height": 175, "crash_severity": 40,
                "seatbelt_system": "SLL", "seat_x_mm": 200.0,
            },
        )

    assert front_resp.json()["context"]["seat_position_zone"] == "front"
    assert front_resp.json()["video"]["filename"] == "50p_front_40_SLL.mp4"
    assert rear_resp.json()["context"]["seat_position_zone"] == "rear"
    assert rear_resp.json()["video"]["filename"] == "50p_rear_40_SLL.mp4"


@pytest.mark.asyncio
async def test_match_falls_back_to_live_can_signal_when_seat_x_param_omitted(monkeypatch, tmp_path):
    """When seat_x_mm is omitted, the backend reads SPS_FL_SeatDirectionX from the signal store."""
    async with await _build_client(
        monkeypatch,
        tmp_path,
        initial_signals={"SPS_FL_SeatDirectionX": 200.0},
        video_names=["50p_rear_40_SLL.mp4"],
    ) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={"weight": 75, "height": 175, "crash_severity": 40, "seatbelt_system": "SLL", "seat": "fl"},
        )

    body = resp.json()
    assert body["context"]["seat_x_source"] == "can_signal"
    assert body["context"]["seat_position_zone"] == "rear"


@pytest.mark.asyncio
async def test_match_can_occupant_classification_overrides_weight_derived_percentile(monkeypatch, tmp_path):
    """OMS_FL_OccupantClassification (if present) overrides the percentile derived from weight."""
    async with await _build_client(
        monkeypatch,
        tmp_path,
        initial_signals={"OMS_FL_OccupantClassification": 3.0},  # 3 → 95th percentile
        video_names=["95p_mid_40_SLL.mp4", "50p_mid_40_SLL.mp4"],
    ) as c:
        resp = await c.get(
            "/api/restraints/match",
            params={"weight": 75, "height": 175, "crash_severity": 40, "seatbelt_system": "SLL", "seat": "fl"},
        )

    body = resp.json()
    assert body["context"]["derived_percentile"] == 50  # derived from weight=75kg
    assert body["context"]["can_percentile"] == 95
    assert body["context"]["effective_percentile"] == 95
    assert body["video"]["filename"] == "95p_mid_40_SLL.mp4"


@pytest.mark.asyncio
async def test_get_video_blocks_path_traversal(monkeypatch, tmp_path):
    """A filename containing '..' (without '/') still reaches the handler and is blocked with 400."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get("/api/restraints/video/..evil.mp4")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_video_encoded_slash_is_rejected_by_routing(monkeypatch, tmp_path):
    """A filename containing an encoded '/' (`%2F`) does not match the 1-segment route → 404 at the router layer."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get("/api/restraints/video/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_video_returns_404_for_missing_file(monkeypatch, tmp_path):
    """A missing file in the media dir returns 404."""
    async with await _build_client(monkeypatch, tmp_path) as c:
        resp = await c.get("/api/restraints/video/does_not_exist.mp4")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_video_serves_existing_file_with_correct_media_type(monkeypatch, tmp_path):
    """An existing file is returned with the Content-Type matching its extension."""
    async with await _build_client(monkeypatch, tmp_path, video_names=["50p_mid_40_SLL.mp4"]) as c:
        resp = await c.get("/api/restraints/video/50p_mid_40_SLL.mp4")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
