"""REST routes for reading real-time signals and history, plus WebSocket push."""

from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path

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
from src.api.routes.profiles import (
    CLIENT_ID_HEADER,
    PROFILE_HEADER,
    build_access_warning,
    get_profile_context,
    is_dev_mode,
    profile_allows_signal,
    profile_has_permission,
    require_profile_permission,
)
from src.api.websocket import ConnectionManager, SubscriptionTopic
from src.can_io.writer import (
    CANWriteRejectedError,
    is_message_writable_by_local_node,
)
from src.core.devmode_locks import get_seat_lock_registry

router = APIRouter()
ws_router = APIRouter()


def _write_owner(request: Request) -> str | None:
    raw = request.headers.get(CLIENT_ID_HEADER)
    return raw.strip()[:128] if raw and raw.strip() else None


def _seat_lock_warning(signal_name: str, lock) -> dict:
    return build_access_warning(
        "devmode_seat_locked",
        f"Seat '{lock.seat}' is reserved by another Dev Mode section for "
        f"{lock.remaining_sec():.0f}s",
        signal_name=signal_name,
        required_permission="write",
    )


def _infer_signal_tags(signal_name: str) -> list[str]:
    return [part for part in signal_name.split("_") if re.match(r'^[A-Z0-9]+$', part)]


@lru_cache(maxsize=1)
def _dbc_signal_configs() -> dict[str, dict]:
    """Signal metadata (min/max/unit/writable/states/tag), merged from every can_db_file in system.json."""
    from src.can_io.parser import DatabaseLoader
    from src.core.config_manager import read_config

    configs: dict[str, dict] = {}
    for ch in read_config().get("can", []):
        can_db_file = ch.get("can_db_file")
        if not can_db_file or not Path(can_db_file).exists():
            continue
        loader = DatabaseLoader()
        try:
            loader.load_dbc(can_db_file)
        except (FileNotFoundError, ValueError, RuntimeError):
            continue
        for msg in loader.messages.values():
            writable = is_message_writable_by_local_node(msg)
            for sig_name, sig in msg.signals.items():
                configs.setdefault(sig_name, {
                    "min_value": sig.minimum,
                    "max_value": sig.maximum,
                    "unit": sig.unit or None,
                    "writable": writable,
                    "states": sig.states or None,
                    "tag": _infer_signal_tags(sig_name) or None,
                })
    return configs


def _batch_access_context(request: Request, required: str) -> tuple[str | None, dict | None, list[dict]]:
    if is_dev_mode(request):
        return None, None, []
    try:
        profile_name, profile, _ = get_profile_context(
            request.headers.get(PROFILE_HEADER),
            client_id=request.headers.get(CLIENT_ID_HEADER),
            allow_bootstrap=False,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else build_access_warning("profile_access_error", str(exc.detail))
        return None, None, [detail]

    if profile is None:
        return None, None, []

    if not profile_has_permission(profile, required):
        return profile_name, profile, [
            build_access_warning(
                "profile_permission_denied",
                f"Profile '{profile_name}' lacks '{required}' permission",
                profile_name=profile_name,
                required_permission=required,
            )
        ]

    return profile_name, profile, []


def _append_filtered_warning(warnings: list[dict], profile_name: str, required: str, skipped: list[str]) -> None:
    if not skipped:
        return
    warnings.append(
        build_access_warning(
            "profile_signal_filtered",
            f"Skipped {len(skipped)} signal(s) outside profile '{profile_name}' scope",
            profile_name=profile_name,
            required_permission=required,
            signals=sorted(skipped),
        )
    )


@router.get("", response_model=SignalListResponse, summary="List latest signal values")
async def list_signals(request: Request):
    store = request.app.state.store
    snapshot = await store.get_snapshot()
    # Profiles restrict TX only; all received values remain readable.
    items = [
        SignalValueResponse(
            signal_name=name,
            std_name=name,
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
    summary="List all available signals with metadata",
)
async def list_available_signals(request: Request):
    """Return the full metadata list for all signals.

    The client calls this once at startup to get the structure, then only subscribes
    to lightweight value + timestamp updates over WebSocket.
    """
    store = request.app.state.store
    snapshot = await store.get_snapshot()
    signal_configs = _dbc_signal_configs()

    items: list[SignalMetadata] = []
    # Merge all known signal names from store + config
    all_names = set(snapshot.keys()) | set(signal_configs.keys())

    for name in sorted(all_names):
        sv = snapshot.get(name)
        sig_cfg = signal_configs.get(name, {})
        std_name = name

        items.append(
            SignalMetadata(
                signal_name=name,
                std_name=std_name,
                unit=sig_cfg.get("unit") or (getattr(sv, "unit", None) if sv else None),
                tag=sig_cfg.get("tag"),
                min_value=sig_cfg.get("min_value"),
                max_value=sig_cfg.get("max_value"),
                writable=sig_cfg.get("writable", False),
                states=sig_cfg.get("states"),
                group_name=None,
                widget_type=None,
                value=sv.value if sv else None,
                timestamp=sv.timestamp if sv else None,
            )
        )
    return SignalMetadataListResponse(signals_info=items, total=len(items))


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
        std_name=signal_name,
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
    require_profile_permission(
        request,
        "write",
        signal_name=signal_name,
        alternates=[signal_name],
        allow_bootstrap=True,
    )
    writer = getattr(request.app.state, "writer", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN writer not available"
        )
    blocking = get_seat_lock_registry().blocking_lock(signal_name, _write_owner(request))
    if blocking is not None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=_seat_lock_warning(signal_name, blocking),
        )
    try:
        await writer.send_signal(signal_name, body.value)
    except CANWriteRejectedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
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
    return {"signal_name": signal_name, "value": body.value, "queued_at": time.time()}


@router.post(
    "/batch_update",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Write multiple writable signals simultaneously (batch)",
)
async def batch_update_signals(body: BatchSignalWrite, request: Request):
    """Write multiple CAN signals at once.

    Signals belonging to the same CAN message are grouped together and sent as a single
    frame. Other signals in the same message that are not included in the
    batch use ``writer.use_prevalue_for_unwritten_signal`` from ``system.json``.
    REST writes are broadcast immediately to all subscribed WS clients.
    """
    writer = getattr(request.app.state, "writer", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN writer not available"
        )
    profile_name, profile, warnings = _batch_access_context(request, "write")
    # Profile resolution errors must stop TX even when no profile was returned.
    if warnings:
        return {"queued": [], "count": 0, "queued_at": time.time(), "errors": [], "warnings": warnings}

    resolved: dict[str, float] = {}
    aliases: dict[str, set[str]] = {}
    for item in body.signals:
        resolved[item.signal_name] = item.value
        aliases.setdefault(item.signal_name, set()).add(item.signal_name)

    if profile is not None:
        skipped = [
            name for name in resolved
            if not profile_allows_signal(profile, name, sorted(aliases.get(name, set())), required="write")
        ]
        _append_filtered_warning(warnings, profile_name, "write", skipped)
        resolved = {
            name: value
            for name, value in resolved.items()
            if profile_allows_signal(profile, name, sorted(aliases.get(name, set())), required="write")
        }

    if not resolved:
        return {"queued": [], "count": 0, "queued_at": time.time(), "errors": [], "warnings": warnings}

    registry = get_seat_lock_registry()
    owner = _write_owner(request)
    allowed: dict[str, float] = {}
    for name, value in resolved.items():
        blocking = registry.blocking_lock(name, owner)
        if blocking is None:
            allowed[name] = value
        else:
            warnings.append(_seat_lock_warning(name, blocking))
    resolved = allowed

    if not resolved:
        return {"queued": [], "count": 0, "queued_at": time.time(), "errors": [], "warnings": warnings}

    sent, errors = await writer.send_signals_batch(resolved)
    queued = [{"signal_name": k, "value": v} for k, v in sent.items()]

    if not queued and any(err.get("kind") == "transport" for err in errors):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=errors,
        )
    if not queued and any(err.get("kind") == "not_tx" for err in errors):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=errors,
        )
    if errors and not queued:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=errors)
    return {"queued": queued, "count": len(queued), "queued_at": time.time(), "errors": errors, "warnings": warnings}


# ── WebSocket ────────────────────────────────────────────────────────────────────────────────────────────────────────


@ws_router.websocket("/signals")
async def ws_signals(websocket: WebSocket, api_key: str | None = Query(None), profile_name: str | None = Query(None)):
    """Primary WebSocket endpoint — compatible with the demo API.

    Client → Server:
        {"type": "subscribe", "signals": ["SignalName", "*", "metrics"]}
        {"type": "unsubscribe", "signals": ["SignalName"]}
        {"type": "ping"}  →  {"type": "pong"}
    Server → Client (signal frame):
        {"timestamp": "2026-05-20T10:00:00.123Z", "signals": [{"name": "...", "std_name": "...", "value": 0.0}]}
    Server → Client (subscribe ack):
        {"type": "subscribed", "signals": [...], "count": N}
    """
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle_subscribe(websocket, profile_name=profile_name)


@ws_router.websocket("/all")
async def ws_all(websocket: WebSocket, api_key: str | None = Query(None)):
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle(websocket, topics={SubscriptionTopic.ALL})


@ws_router.websocket("/subscribe")
async def ws_subscribe(websocket: WebSocket, api_key: str | None = Query(None), profile_name: str | None = Query(None)):
    """Alias of /ws/signals — kept for backward compatibility. /ws/signals is recommended for new clients.

    Supports both formats:
        {"type": "subscribe", "signals": ["EngineSpeed", "*"]}   # demo format
        {"action": "subscribe", "channels": ["metrics"]}          # legacy format
        {"type": "ping"}  →  {"type": "pong"}
    """
    auth = websocket.app.state.auth
    if not auth.verify(api_key):
        await websocket.close(code=4401)
        return
    mgr: ConnectionManager = websocket.app.state.ws_manager
    await mgr.handle_subscribe(websocket, profile_name=profile_name)
