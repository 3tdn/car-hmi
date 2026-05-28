"""Route kiểm tra sức khỏe, trạng thái sẵn dùng và thông tin tài nguyên hệ thống."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from src.api.models import HealthResponse, ReadinessResponse, SystemInfoResponse, SystemMetricsResponse
from src.core.system_metrics import collect_system_metrics, metrics_to_dict

router = APIRouter()


@router.get(
    "/info",
    response_model=SystemInfoResponse,
    summary="Get project & system information",
)
async def system_info(request: Request) -> SystemInfoResponse:
    """Thông tin tổng quan dự án: tên, phiên bản, uptime, trạng thái kết nối, số tín hiệu."""
    uptime = time.time() - request.app.state.start_time
    readers = getattr(request.app.state, "readers", None)
    bus_ok = bool(readers and any(getattr(r, "_bus", None) is not None for r in readers))
    db_ok = bool(request.app.state.repo)
    store = request.app.state.store
    snapshot = await store.get_snapshot()
    return SystemInfoResponse(
        name="CAN-HMI Signal API",
        version="1.0.0",
        description="Real-time CAN bus signal monitoring and control API",
        uptime_seconds=round(uptime, 1),
        bus_connected=bus_ok,
        db_connected=db_ok,
        signal_count=len(snapshot),
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health(request: Request) -> HealthResponse:
    uptime = time.time() - request.app.state.start_time
    readers = getattr(request.app.state, "readers", None)
    bus_ok = bool(
        readers
        and any(getattr(r, "_bus", None) is not None for r in readers)
    )
    db_ok = bool(request.app.state.repo)
    overall = "ok" if (bus_ok and db_ok) else "degraded"
    return HealthResponse(
        status=overall,
        uptime_seconds=round(uptime, 1),
        bus_connected=bus_ok,
        db_connected=db_ok,
    )


@router.get(
    "/ready", response_model=ReadinessResponse, summary="Readiness probe (for container/systemd)"
)
async def ready(request: Request) -> ReadinessResponse:
    readers = getattr(request.app.state, "readers", None)
    bus_ok = bool(
        readers
        and any(getattr(r, "_bus", None) is not None for r in readers)
    )
    db_ok = bool(request.app.state.repo)
    details = {"bus": bus_ok, "db": db_ok}
    return ReadinessResponse(ready=all(details.values()), details=details)


@router.get(
    "/metrics",
    response_model=SystemMetricsResponse,
    summary="Thông tin tài nguyên CarPC (CPU, RAM, disk, queue, heap…)",
)
async def system_metrics(request: Request) -> SystemMetricsResponse:
    """Thu thập và trả về thông tin tài nguyên hệ thống CarPC."""
    rx_queue = getattr(request.app.state, "rx_queue", None)
    start_time = getattr(request.app.state, "start_time", 0.0)
    m = collect_system_metrics(rx_queue=rx_queue, start_time=start_time)
    return SystemMetricsResponse(**metrics_to_dict(m))
