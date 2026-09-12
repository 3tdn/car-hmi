"""Routes for health checks, readiness status, and system resource information."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, status

from src.api.models import (
    HealthResponse,
    ReadinessResponse,
    SystemInfoResponse,
    SystemMetricsResponse,
)
from src.api.routes.profiles import is_dev_mode
from src.core.system_metrics import collect_system_metrics, metrics_to_dict

router = APIRouter()


def _require_control_auth(request: Request) -> None:
    """Allow state-changing system operations only for authenticated Dev Mode calls."""
    auth = getattr(request.app.state, "auth", None)
    if auth is None or not auth.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System controls require a configured non-placeholder API key",
        )
    api_key = request.headers.get("X-API-Key")
    if not auth.verify(api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")
    if not is_dev_mode(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev Mode is required for this operation",
        )


def _summarize_readers(readers, stale_threshold_sec: float) -> dict[str, bool]:
    """Quickly assess reader health based on thread status, last frame, and the fatal flag."""
    if not readers:
        return {
            "readers_present": False,
            "readers_thread_alive": False,
            "readers_recent_frames": False,
            "readers_no_fatal_error": False,
            "bus": False,
        }

    states: list[dict] = []
    now = time.time()
    for reader in readers:
        if hasattr(reader, "get_runtime_state"):
            state = reader.get_runtime_state()
        else:
            state = {
                "thread_alive": bool(getattr(reader, "_bus", None) is not None),
                "last_frame_timestamp": 0.0,
                "fatal_error": None,
            }
        last_ts = float(state.get("last_frame_timestamp") or 0.0)
        state["frame_recent"] = bool(
            stale_threshold_sec <= 0
            or (last_ts > 0 and (now - last_ts) <= stale_threshold_sec)
        )
        states.append(state)

    readers_thread_alive = all(bool(s.get("thread_alive")) for s in states)
    readers_recent_frames = all(bool(s.get("frame_recent")) for s in states)
    readers_no_fatal_error = all(not s.get("fatal_error") for s in states)
    bus_connected = readers_thread_alive and readers_recent_frames and readers_no_fatal_error

    return {
        "readers_present": True,
        "readers_thread_alive": readers_thread_alive,
        "readers_recent_frames": readers_recent_frames,
        "readers_no_fatal_error": readers_no_fatal_error,
        "bus": bus_connected,
    }


@router.get(
    "/info",
    response_model=SystemInfoResponse,
    summary="Get project & system information",
)
async def system_info(request: Request) -> SystemInfoResponse:
    """Project overview: name, version, uptime, connection status, signal count."""
    uptime = time.time() - request.app.state.start_time
    readers = getattr(request.app.state, "readers", None)
    stale_threshold_sec = float(getattr(request.app.state, "reader_stale_threshold_sec", 30.0))
    reader_summary = _summarize_readers(readers, stale_threshold_sec)
    bus_ok = reader_summary["bus"]
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
    stale_threshold_sec = float(getattr(request.app.state, "reader_stale_threshold_sec", 30.0))
    reader_summary = _summarize_readers(readers, stale_threshold_sec)
    bus_ok = reader_summary["bus"]
    db_ok = bool(request.app.state.repo)
    if bus_ok and db_ok:
        overall = "ok"
    elif reader_summary["readers_present"] and (not reader_summary["readers_no_fatal_error"]):
        overall = "error"
    else:
        overall = "degraded"
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
    stale_threshold_sec = float(getattr(request.app.state, "reader_stale_threshold_sec", 30.0))
    reader_summary = _summarize_readers(readers, stale_threshold_sec)
    bus_ok = reader_summary["bus"]
    db_ok = bool(request.app.state.repo)
    details = {
        "bus": bus_ok,
        "db": db_ok,
        "readers_thread_alive": reader_summary["readers_thread_alive"],
        "readers_recent_frames": reader_summary["readers_recent_frames"],
        "readers_no_fatal_error": reader_summary["readers_no_fatal_error"],
    }
    return ReadinessResponse(ready=all(details.values()), details=details)


@router.get(
    "/metrics",
    response_model=SystemMetricsResponse,
    summary="CarPC resource information (CPU, RAM, disk, queue, heap…)",
)
async def system_metrics(request: Request) -> SystemMetricsResponse:
    """Collect and return CarPC system resource information."""
    rx_queue = getattr(request.app.state, "rx_queue", None)
    start_time = getattr(request.app.state, "start_time", 0.0)
    m = collect_system_metrics(rx_queue=rx_queue, start_time=start_time)
    return SystemMetricsResponse(**metrics_to_dict(m))


@router.post("/can/retry", summary="Retry CAN connections")
async def retry_can_connections(request: Request) -> dict:
    """Close stale CAN buses and immediately begin their reconnect schedules."""
    _require_control_auth(request)
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN runner unavailable")
    scheduled = await runner.retry_can_connections()
    return {"scheduled": scheduled, "count": sum(scheduled)}


@router.post("/reboot", status_code=status.HTTP_202_ACCEPTED, summary="Reboot Car-HMI service")
async def reboot(request: Request) -> dict:
    """Gracefully exit so the systemd service restarts the Car-HMI process."""
    _require_control_auth(request)
    runner = getattr(request.app.state, "runner", None)
    if runner is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN runner unavailable")
    if not await runner.request_reboot():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Reboot already in progress")
    return {"status": "reboot_scheduled"}
