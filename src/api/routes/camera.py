"""Route proxy camera stream (MJPEG).

Camera phát MJPEG qua CarPC tại 1 URL cố định (xem `config/system.json` →
`camera.stream_url`, mặc định `http://192.168.2.119:8080/stream`). MJPG
server phía nguồn chỉ cho phép DUY NHẤT 1 kết nối đồng thời (mutex phía
nguồn) — CarPC mở đúng 1 kết nối upstream (`CameraStreamProxy`) và fan-out
cho nhiều thiết bị đầu cuối (nhiều user) xem đồng thời qua route `/stream`
dưới đây, mỗi client không đụng trực tiếp tới camera nên không vi phạm giới
hạn 1-connection của nguồn.
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
    """Stream MJPEG video tới client hiện tại.

    Nhiều client có thể mở endpoint này đồng thời — CarPC dùng chung 1 kết
    nối upstream tới camera (giới hạn mutex 1-receiver phía nguồn) và
    fan-out dữ liệu cho tất cả client.
    """
    proxy = _get_proxy(request)
    queue = await proxy.open_subscription()
    return StreamingResponse(proxy.stream_queue(queue), media_type=proxy.content_type)


@router.get("/status", response_model=CameraStatusResponse, summary="Camera stream proxy status")
async def camera_status(request: Request) -> CameraStatusResponse:
    """Trạng thái hiện tại của camera stream proxy: kết nối, số viewer, lỗi gần nhất."""
    proxy = _get_proxy(request)
    return CameraStatusResponse(
        enabled=True,
        stream_url=proxy.stream_url,
        connected=proxy.connected,
        viewer_count=proxy.viewer_count,
        last_error=proxy.last_error,
    )
