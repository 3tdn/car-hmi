"""Route REST để đọc tín hiệu thời gian thực và lịch sử, cộng push qua WebSocket."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, status

from src.api.models import (
    SignalListResponse,
    SignalMetadata,
    SignalMetadataListResponse,
    SignalValueResponse,
    WriteSignalRequest,
)
from src.api.websocket import ConnectionManager, SubscriptionTopic

router = APIRouter()
ws_router = APIRouter()


@router.get("", response_model=SignalListResponse, summary="List latest signal values")
async def list_signals(request: Request):
    store = request.app.state.store
    snapshot = await store.get_snapshot()
    items = [
        SignalValueResponse(
            signal_name=name,
            value=sv.value,
            unit=getattr(sv, "unit", None),
            timestamp=sv.timestamp,
        )
        for name, sv in snapshot.items()
    ]
    return SignalListResponse(items=items, total=len(items))


# ── Available signals (full metadata, one-time fetch) ────────────────────────
# IMPORTANT: must be registered BEFORE /{signal_name} to avoid path conflict.


@router.get(
    "/available",
    response_model=SignalMetadataListResponse,
    summary="List all available signals with metadata and alarm thresholds",
)
async def list_available_signals(request: Request):
    """Trả về danh sách đầy đủ metadata của tất cả tín hiệu.

    Client gọi 1 lần khi khởi động để lấy cấu trúc, sau đó chỉ subscribe
    value + timestamp nhẹ qua WebSocket.
    """
    from pathlib import Path

    import json

    from src.core.config_manager import read_alarms

    store = request.app.state.store
    snapshot = await store.get_snapshot()

    # Load signal configs
    signal_configs: dict = {}
    signals_path = Path("config/signals.json")
    if signals_path.exists():
        raw = json.loads(signals_path.read_text(encoding="utf-8")) or {}
        signal_configs = raw.get("signals", {})

    # Load alarm configs
    alarm_raw = read_alarms()
    alarm_configs = alarm_raw.get("alarms", {})

    items: list[SignalMetadata] = []
    # Merge all known signal names from store + config
    all_names = set(snapshot.keys()) | set(signal_configs.keys())

    for name in sorted(all_names):
        sv = snapshot.get(name)
        sig_cfg = signal_configs.get(name, {})
        alm_cfg = alarm_configs.get(name, {})

        items.append(
            SignalMetadata(
                signal_name=name,
                unit=getattr(sv, "unit", None) if sv else None,
                min_value=sig_cfg.get("min_value"),
                max_value=sig_cfg.get("max_value"),
                writable=sig_cfg.get("writable", False),
                group_name=sig_cfg.get("group"),
                widget_type=sig_cfg.get("widget"),
                alarm_warning_high=alm_cfg.get("warning_high"),
                alarm_warning_low=alm_cfg.get("warning_low"),
                alarm_critical_high=alm_cfg.get("critical_high"),
                alarm_critical_low=alm_cfg.get("critical_low"),
                value=sv.value if sv else None,
                status=sv.status if sv else None,
                timestamp=sv.timestamp if sv else None,
            )
        )
    return SignalMetadataListResponse(items=items, total=len(items))


@router.get(
    "/{signal_name}", response_model=SignalValueResponse, summary="Get latest value for one signal"
)
async def get_signal(signal_name: str, request: Request):
    store = request.app.state.store
    sv = await store.get(signal_name)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal '{signal_name}' not found"
        )
    return SignalValueResponse(
        signal_name=signal_name,
        value=sv.value,
        unit=getattr(sv, "unit", None),
        timestamp=sv.timestamp,
    )


@router.get(
    "/{signal_name}/history",
    response_model=SignalListResponse,
    summary="Query signal history from DB",
)
async def get_signal_history(
    signal_name: str,
    request: Request,
    start: float | None = Query(None),
    end: float | None = Query(None),
    limit: int = Query(100, ge=1, le=10_000),
    offset: int = Query(0, ge=0),
):
    repo = request.app.state.repo
    records = await repo.query_signals(
        signal_name=signal_name, start=start, end=end, limit=limit, offset=offset
    )
    items = [
        SignalValueResponse(
            signal_name=r.signal_name, value=r.value, unit=r.unit, timestamp=r.timestamp
        )
        for r in records
    ]
    return SignalListResponse(items=items, total=len(items))


@router.put(
    "/{signal_name}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Write value to signal (CAN write)",
)
async def write_signal(signal_name: str, body: WriteSignalRequest, request: Request):
    writer = getattr(request.app.state, "writer", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN writer not available"
        )
    await writer.send_signal(signal_name, body.value)
    return {"signal_name": signal_name, "value": body.value, "queued_at": time.time()}


# ── WebSocket ────────────────────────────────────────────────────────────────────────────────────────────────────────


@ws_router.websocket("/signals")
async def ws_signals(websocket: WebSocket):
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle(websocket, topics={SubscriptionTopic.SIGNALS})


@ws_router.websocket("/alarms")
async def ws_alarms(websocket: WebSocket):
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle(websocket, topics={SubscriptionTopic.ALARMS})


@ws_router.websocket("/all")
async def ws_all(websocket: WebSocket):
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle(websocket, topics={SubscriptionTopic.ALL})


@ws_router.websocket("/subscribe")
async def ws_subscribe(websocket: WebSocket):
    """Endpoint mới: client gửi JSON subscribe/unsubscribe để chọn kênh nhận dữ liệu.

    Message format từ client:
        {"action": "subscribe", "channels": ["EngineSpeed", "alarms", "metrics"], "mode": "continuous"}
        {"action": "unsubscribe", "channels": ["EngineSpeed"]}
    """
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle_subscribe(websocket)
