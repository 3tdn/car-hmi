"""Route REST để đọc tín hiệu thời gian thực và lịch sử, cộng push qua WebSocket."""

from __future__ import annotations

import time

import can
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, status

from src.api.models import (
    BatchSignalWrite,
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
    mapper = getattr(request.app.state, "signal_name_mapper", None)
    snapshot = await store.get_snapshot()
    items = [
        SignalValueResponse(
            signal_name=name,
            std_name=mapper.get_std_name(name) if mapper else None,
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
    import json
    from pathlib import Path

    from src.core.config_manager import read_alarms, read_config

    store = request.app.state.store
    snapshot = await store.get_snapshot()

    # Load signal configs from all can_json_path files listed in system.json
    signal_configs: dict[str, dict] = {}
    sys_cfg = read_config()
    for ch in sys_cfg.get("can", []):
        can_json_path = Path(ch.get("can_json_path", ""))
        if not can_json_path.exists():
            continue
        ch_raw = json.loads(can_json_path.read_text(encoding="utf-8")) or {}
        for msg_data in ch_raw.get("messages", {}).values():
            for sig_name, sig_data in msg_data.get("signals", {}).items():
                signal_configs.setdefault(sig_name, {
                    "min_value": sig_data.get("minimum"),
                    "max_value": sig_data.get("maximum"),
                    "unit": sig_data.get("unit") or None,
                    "writable": bool(sig_data.get("TX", False)),
                    "states": sig_data.get("states") or None,
                })

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
                std_name=sig_cfg.get("std_name") if "std_name" in sig_cfg else (
                    request.app.state.signal_name_mapper.get_std_name(name)
                    if hasattr(request.app.state, "signal_name_mapper") else None
                ),
                unit=sig_cfg.get("unit") or (getattr(sv, "unit", None) if sv else None),
                min_value=sig_cfg.get("min_value"),
                max_value=sig_cfg.get("max_value"),
                writable=sig_cfg.get("writable", False),
                states=sig_cfg.get("states"),
                group_name=None,
                widget_type=None,
                alarm_warning_high=alm_cfg.get("warning_high"),
                alarm_warning_low=alm_cfg.get("warning_low"),
                alarm_critical_high=alm_cfg.get("critical_high"),
                alarm_critical_low=alm_cfg.get("critical_low"),
                value=sv.value if sv else None,
                status=sv.status if sv else None,
                timestamp=sv.timestamp if sv else None,
            )
        )
    return SignalMetadataListResponse(signals_info=items, total=len(items))


@router.get(
    "/{signal_name}", response_model=SignalValueResponse, summary="Get latest value for one signal"
)
async def get_signal(signal_name: str, request: Request):
    mapper = getattr(request.app.state, "signal_name_mapper", None)
    canonical = mapper.resolve(signal_name) if mapper else signal_name
    store = request.app.state.store
    sv = await store.get(canonical)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal '{signal_name}' not found"
        )
    return SignalValueResponse(
        signal_name=canonical,
        std_name=mapper.get_std_name(canonical) if mapper else None,
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
    mapper = getattr(request.app.state, "signal_name_mapper", None)
    canonical = mapper.resolve(signal_name) if mapper else signal_name
    repo = request.app.state.repo
    records = await repo.query_signals(
        signal_name=canonical, start=start, end=end, limit=limit, offset=offset
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
    mapper = getattr(request.app.state, "signal_name_mapper", None)
    canonical = mapper.resolve(signal_name) if mapper else signal_name
    writer = getattr(request.app.state, "writer", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN writer not available"
        )
    try:
        await writer.send_signal(canonical, body.value)
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower() or "cannot encode" in message.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=message,
        ) from exc
    except can.CanError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"signal_name": canonical, "value": body.value, "queued_at": time.time()}


@router.post(
    "/batch_update",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Write multiple writable signals simultaneously (batch)",
)
async def batch_update_signals(body: BatchSignalWrite, request: Request):
    """Ghi nhiều tín hiệu CAN cùng lúc.

    Các tín hiệu thuộc cùng một CAN message được gộp lại và gửi thành một
    frame duy nhất (read-modify-write: giữ nguyên giá trị các tín hiệu khác
    trong cùng message không có trong danh sách batch).
    REST write được broadcast ngay tới tất cả WS clients đang subscribe.
    """
    writer = getattr(request.app.state, "writer", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN writer not available"
        )
    mapper = getattr(request.app.state, "signal_name_mapper", None)

    # Resolve canonical names; last entry wins nếu trùng tên sau resolve
    resolved: dict[str, float] = {}
    for item in body.signals:
        canonical = mapper.resolve(item.signal_name) if mapper else item.signal_name
        resolved[canonical] = item.value

    sent, errors = await writer.send_signals_batch(resolved)
    queued = [{"signal_name": k, "value": v} for k, v in sent.items()]

    if not queued and any(err.get("kind") == "transport" for err in errors):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=errors,
        )
    if errors and not queued:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=errors)
    return {"queued": queued, "count": len(queued), "queued_at": time.time(), "errors": errors}


# ── WebSocket ────────────────────────────────────────────────────────────────────────────────────────────────────────


@ws_router.websocket("/signals")
async def ws_signals(websocket: WebSocket, api_key: str | None = Query(None)):
    """Endpoint WebSocket chính — tương thích với demo API.

    Client → Server:
        {"type": "subscribe", "signals": ["SignalName", "*", "alarms", "metrics"]}
        {"type": "unsubscribe", "signals": ["SignalName"]}
        {"type": "ping"}  →  {"type": "pong"}
    Server → Client (signal frame):
        {"timestamp": "2026-05-20T10:00:00.123Z", "signals": [{"name": "...", "value": 0.0}]}
    Server → Client (subscribe ack):
        {"type": "subscribed", "signals": [...], "count": N}
    """
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle_subscribe(websocket)


@ws_router.websocket("/alarms")
async def ws_alarms(websocket: WebSocket, api_key: str | None = Query(None)):
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle(websocket, topics={SubscriptionTopic.ALARMS})


@ws_router.websocket("/all")
async def ws_all(websocket: WebSocket, api_key: str | None = Query(None)):
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle(websocket, topics={SubscriptionTopic.ALL})


@ws_router.websocket("/subscribe")
async def ws_subscribe(websocket: WebSocket, api_key: str | None = Query(None)):
    """Alias của /ws/signals — giữ để backward compatible. Khuyến nghị dùng /ws/signals cho client mới.

    Hỗ trợ đồng thời cả 2 định dạng:
        {"type": "subscribe", "signals": ["EngineSpeed", "*"]}   # demo format
        {"action": "subscribe", "channels": ["metrics"]}          # legacy format
        {"type": "ping"}  →  {"type": "pong"}
    """
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle_subscribe(websocket)
