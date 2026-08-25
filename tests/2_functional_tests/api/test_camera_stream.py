"""Tests for CameraStreamProxy (single-upstream fan-out) and camera routes."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import camera as camera_route
from src.core.camera_stream import CameraStreamProxy


class _FakeResponse:
    """Mimics an httpx streaming response over a fixed set of byte chunks."""

    def __init__(self, chunks, content_type="multipart/x-mixed-replace; boundary=frame"):
        self._chunks = chunks
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass

    async def aiter_bytes(self, chunk_size):
        for chunk in self._chunks:
            yield chunk
            await asyncio.sleep(0)


class _FakeStreamCtx:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    """Drop-in replacement for httpx.AsyncClient used by CameraStreamProxy."""

    chunks: ClassVar[list[bytes]] = [b"frame1", b"frame2", b"frame3"]

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        return _FakeStreamCtx(_FakeResponse(self.chunks))


@pytest.fixture(autouse=True)
def _patch_httpx(monkeypatch):
    monkeypatch.setattr("src.core.camera_stream.httpx.AsyncClient", _FakeAsyncClient)


async def _collect(proxy: CameraStreamProxy, queue, n: int):
    chunks = []
    async for chunk in proxy.stream_queue(queue):
        chunks.append(chunk)
        if len(chunks) >= n:
            break
    return chunks


async def test_single_subscriber_receives_frames():
    proxy = CameraStreamProxy("http://fake/stream", reconnect_interval_sec=0.05, startup_wait_sec=1.0)
    try:
        queue = await proxy.open_subscription()
        assert proxy.viewer_count == 1
        assert proxy.content_type == "multipart/x-mixed-replace; boundary=frame"

        chunks = await asyncio.wait_for(_collect(proxy, queue, 3), timeout=2)
        assert chunks == [b"frame1", b"frame2", b"frame3"]
    finally:
        await proxy.aclose()


async def test_multiple_subscribers_fan_out_from_single_upstream():
    proxy = CameraStreamProxy("http://fake/stream", reconnect_interval_sec=0.05, startup_wait_sec=1.0)
    try:
        q1 = await proxy.open_subscription()
        q2 = await proxy.open_subscription()
        assert proxy.viewer_count == 2

        c1, c2 = await asyncio.gather(
            asyncio.wait_for(_collect(proxy, q1, 3), timeout=2),
            asyncio.wait_for(_collect(proxy, q2, 3), timeout=2),
        )
        valid_frames = {b"frame1", b"frame2", b"frame3"}
        # Both viewers share the same single upstream connection (fan-out), so each
        # must observe 3 valid frames — but not necessarily starting at the same
        # offset, since the second subscriber may join mid-cycle.
        assert len(c1) == 3 and set(c1) <= valid_frames
        assert len(c2) == 3 and set(c2) <= valid_frames
    finally:
        await proxy.aclose()


async def test_viewer_count_decrements_on_unsubscribe():
    proxy = CameraStreamProxy("http://fake/stream", reconnect_interval_sec=0.05, startup_wait_sec=1.0)
    try:
        queue = await proxy.open_subscription()
        assert proxy.viewer_count == 1
        await proxy._remove_subscriber(queue)
        assert proxy.viewer_count == 0
    finally:
        await proxy.aclose()


def test_track_fps_counts_jpeg_eoi_markers_and_logs(caplog):
    proxy = CameraStreamProxy("http://fake/stream", fps_log_interval_sec=0.0)
    proxy._fps_window_start -= 1.0  # force elapsed > 0 so fps computation doesn't divide by ~0

    with caplog.at_level("INFO", logger="src.core.camera_stream"):
        # 2 full frames in one chunk
        proxy._track_fps(b"\xff\xd8...\xff\xd9\xff\xd8...\xff\xd9")

    assert proxy.fps > 0
    assert any("Camera upstream FPS" in r.message for r in caplog.records)


def test_track_fps_detects_marker_split_across_chunks():
    proxy = CameraStreamProxy("http://fake/stream", fps_log_interval_sec=100.0)
    # marker 0xFF 0xD9 split across two consecutive chunks
    proxy._track_fps(b"\xff\xd8...\xff")
    proxy._track_fps(b"\xd9\xff\xd8...")
    assert proxy._frame_count == 1


async def test_camera_status_route_reports_viewer_count():
    app = FastAPI()
    app.include_router(camera_route.router, prefix="/api/camera")

    class _FakeProxy:
        stream_url = "http://192.168.2.119:8080/stream"
        connected = True
        viewer_count = 2
        last_error = None

    app.state.camera_proxy = _FakeProxy()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/camera/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["viewer_count"] == 2
    assert data["connected"] is True
    assert data["stream_url"] == "http://192.168.2.119:8080/stream"


async def test_camera_status_route_503_when_not_configured():
    app = FastAPI()
    app.include_router(camera_route.router, prefix="/api/camera")
    app.state.camera_proxy = None

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/camera/status")

    assert resp.status_code == 503
