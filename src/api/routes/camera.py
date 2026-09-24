"""Route proxy camera stream (MJPEG).

The camera publishes MJPEG through the CarPC at a fixed URL (see `config/system.json` →
`camera.stream_url`, default `http://192.168.2.119:8080/stream`). The upstream MJPG
server allows only ONE concurrent connection (source-side
mutex) — the CarPC opens exactly 1 upstream connection (`CameraStreamProxy`) and fans it out
to multiple end devices (multiple users) through the `/stream` route
below; each client does not connect directly to the camera, so it does not violate the
source's 1-connection limit.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.api.models import CameraStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_proxy(request: Request):
    proxy = getattr(request.app.state, "camera_proxy", None)
    if proxy is None:
        raise HTTPException(status_code=503, detail="Camera stream not configured/enabled")
    return proxy


@router.get("/stream", summary="Proxy live MJPEG stream from the vehicle camera")
async def camera_stream(request: Request) -> StreamingResponse:
    """Stream MJPEG video to the current client.

    Multiple clients can open this endpoint simultaneously — the CarPC shares 1
    upstream connection to the camera (source-side 1-receiver mutex limit) and
    fans out the data to all clients.
    """
    proxy = _get_proxy(request)
    queue = await proxy.open_subscription()
    return StreamingResponse(proxy.stream_queue(queue), media_type=proxy.content_type)


@router.get("/status", response_model=CameraStatusResponse, summary="Camera stream proxy status")
async def camera_status(request: Request) -> CameraStatusResponse:
    """Current camera stream proxy status: connection, viewer count, latest error."""
    proxy = _get_proxy(request)
    return CameraStatusResponse(
        enabled=True,
        stream_url=proxy.stream_url,
        connected=proxy.connected,
        viewer_count=proxy.viewer_count,
        last_error=proxy.last_error,
    )
