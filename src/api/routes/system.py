"""Route kiểm tra sức khỏe, trạng thái sẵn dùng và thông tin tài nguyên hệ thống."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

from src.api.models import HealthResponse, ReadinessResponse, SystemMetricsResponse
from src.core.system_metrics import collect_system_metrics, metrics_to_dict

router = APIRouter()


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(request: Request) -> HealthResponse:
    uptime = time.time() - request.app.state.start_time
    reader = getattr(request.app.state, "reader", None)
    bus_ok = bool(reader and getattr(reader, "_bus", None) is not None)
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
    reader = getattr(request.app.state, "reader", None)
    bus_ok = bool(reader and getattr(reader, "_bus", None) is not None)
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
