"""REST routes for Dev Mode — select seats and drive a signal on several seats at once.

See `docs/devmode_api.md`. When a section selects a seat, the backend holds a
per-seat write lock for `block_timeout_sec` (60s by default) that blocks other
sections until it expires or the owning section leaves Dev Mode.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import suppress
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import can
from fastapi import APIRouter, HTTPException, Request, status

from src.api.models import DevModeSeatSelectRequest, DevModeSignalRequest
from src.api.routes.profiles import CLIENT_ID_HEADER
from src.core.devmode_locks import SEAT_IDS, SeatLock, get_seat_lock_registry

router = APIRouter()
logger = logging.getLogger(__name__)

# Signal family → real CAN signal name templates per seat ({seat} = FL/FR/RL1/RL2/RR1)
SIGNAL_FAMILIES: dict[str, dict] = {
    "ACR_RetractRequest": {
        "kind": "state",
        "templates": ["ACR_{seat}_RetractRequest"],
        "allowed_values": [5, *range(10, 26)],
        "fallback_states": [{"value": 5, "description": "Haptic"}]
        + [{"value": v, "description": f"Retract level {v}"} for v in range(10, 26)],
    },
    "ABL_RetractRequest": {
        "kind": "state",
        "templates": ["ABL_{seat}_RetractRequest"],
        "allowed_values": [0, 1, 2, 3, 4, 5, 11, 12],
        "fallback_states": [],
    },
    "ISB_Color": {
        "kind": "color",
        "templates": ["ISB_{seat}_ColorRed", "ISB_{seat}_ColorGreen", "ISB_{seat}_ColorBlue"],
        "allowed_values": None,  # 0x000000..0xFFFFFF
        "fallback_states": [
            {"value": 0, "description": "Off"},
            {"value": 0xFF0000, "description": "Red"},
            {"value": 0x00FF00, "description": "Green"},
            {"value": 0x0000FF, "description": "Blue"},
            {"value": 0xFFFF00, "description": "Yellow"},
            {"value": 0x00FFFF, "description": "Cyan"},
            {"value": 0xFF00FF, "description": "Magenta"},
            {"value": 0xFFFFFF, "description": "White"},
        ],
    },
    "HB_Request": {
        "kind": "state",
        "templates": ["HB_Request_{seat}"],
        "allowed_values": [0, 1, 2],
        "fallback_states": [
            {"value": 0, "description": "Off"},
            {"value": 1, "description": "Level 1"},
            {"value": 2, "description": "Level 2"},
        ],
    },
}

# Signal used to tell whether a seat ECU is online
_SEAT_COM_SIGNAL = "COM_Status_Puma{seat}Can"


def _iso_now(timestamp: float | None = None) -> str:
    # timezone.utc instead of datetime.UTC to stay compatible with Python 3.10.
    moment = datetime.fromtimestamp(timestamp if timestamp is not None else time.time(), tz=timezone.utc)  # noqa: UP017
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _devmode_config() -> dict:
    try:
        from src.core.config_manager import read_config

        cfg = read_config().get("devmode", {})
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _default_timeout() -> float:
    try:
        return max(1.0, float(_devmode_config().get("block_timeout_sec", 60.0)))
    except (TypeError, ValueError):
        return 60.0


def _require_seat_connected() -> bool:
    return bool(_devmode_config().get("require_seat_connected", True))


def _registry():
    return get_seat_lock_registry(_default_timeout())


async def _devmode_offline_cleanup_loop(app) -> None:
    """Release locks of offline sessions while Dev Mode has active seat locks."""
    interval = max(1.0, float(getattr(app.state, "devmode_cleanup_interval_sec", 5.0)))
    while not getattr(app.state, "shutting_down", False):
        now = time.time()
        active_locks = _registry().active_locks(now)
        if not active_locks:
            break
        try:
            from src.api.routes import profiles as profile_routes

            owner_ids = {lock.owner for lock in active_locks.values()}
            released_clients = profile_routes.release_devmode_locks_for_offline_sessions(
                now=now,
                owner_ids=owner_ids,
            )
            if released_clients:
                logger.info(
                    "Released devmode locks for %d offline client session(s): %s",
                    len(released_clients),
                    ",".join(sorted(released_clients)),
                )
        except Exception:  # pragma: no cover - defensive guard for background loop
            logger.exception("Devmode offline cleanup loop failed")
        await asyncio.sleep(interval)

    app.state.profile_session_cleanup_task = None


def _ensure_devmode_cleanup_task(request: Request) -> None:
    app = request.app
    task = getattr(app.state, "profile_session_cleanup_task", None)
    if task is not None and not task.done():
        return
    if not _registry().active_locks():
        app.state.profile_session_cleanup_task = None
        return
    app.state.profile_session_cleanup_task = asyncio.create_task(_devmode_offline_cleanup_loop(app))


async def _stop_devmode_cleanup_task_if_idle(request: Request) -> None:
    if _registry().active_locks():
        return
    task = getattr(request.app.state, "profile_session_cleanup_task", None)
    request.app.state.profile_session_cleanup_task = None
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _require_owner(request: Request) -> str:
    raw = request.headers.get(CLIENT_ID_HEADER)
    if not raw or not raw.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Header '{CLIENT_ID_HEADER}' is required for Dev Mode lock operations",
        )
    return raw.strip()[:128]


def _normalize_seats(seats: dict[str, bool]) -> dict[str, bool]:
    normalized: dict[str, bool] = {}
    for raw_seat, flag in seats.items():
        seat = str(raw_seat).strip().lower()
        if seat not in SEAT_IDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown seat '{raw_seat}'. Valid seats: {list(SEAT_IDS)}",
            )
        normalized[seat] = bool(flag)
    return normalized


@lru_cache(maxsize=1)
def _dbc_signal_states() -> dict[str, list[dict]]:
    """States per signal, merged from every can_json_path listed in config/system.json."""
    from src.core.config_manager import read_config

    states: dict[str, list[dict]] = {}
    for channel in read_config().get("can", []):
        path = Path(channel.get("can_json_path", ""))
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            continue
        for message in raw.get("messages", {}).values():
            for name, signal in message.get("signals", {}).items():
                states[name] = signal.get("states") or []
    return states


def _family_states(family: str, spec: dict) -> list[dict]:
    reference = spec["templates"][0].format(seat="FL")
    dbc_states = _dbc_signal_states().get(reference) or []
    allowed = spec.get("allowed_values")
    if dbc_states:
        if allowed is None:
            return dbc_states
        allowed_set = set(allowed)
        filtered = [s for s in dbc_states if s.get("value") in allowed_set]
        if filtered:
            return filtered
    return spec.get("fallback_states") or []


def _expand_signals(family: str, seat: str, value: float) -> dict[str, float]:
    """Map (signal family, seat, value) to the real CAN signals that must be written."""
    spec = SIGNAL_FAMILIES[family]
    seat_token = seat.upper()
    if spec["kind"] == "color":
        rgb = int(value) & 0xFFFFFF
        components = ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
        return {
            template.format(seat=seat_token): float(component)
            for template, component in zip(spec["templates"], components, strict=True)
        }
    return {template.format(seat=seat_token): float(value) for template in spec["templates"]}


def _validate_value(family: str, value: float) -> None:
    spec = SIGNAL_FAMILIES[family]
    if spec["kind"] == "color":
        if not 0 <= int(value) <= 0xFFFFFF:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ISB_Color must be an integer RGB value between 0 and 16777215",
            )
        return
    allowed = spec.get("allowed_values")
    if allowed and int(value) not in set(allowed):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Value {value} is not valid for '{family}'. Allowed: {sorted(set(allowed))}",
        )


async def _seat_connectivity(request: Request) -> dict[str, bool]:
    """Return live seat connectivity; missing and stale signals are disconnected."""
    if not _require_seat_connected():
        return dict.fromkeys(SEAT_IDS, True)
    store = getattr(request.app.state, "store", None)
    if store is None:
        return dict.fromkeys(SEAT_IDS, True)
    try:
        snapshot = await store.get_snapshot()
    except Exception:
        return dict.fromkeys(SEAT_IDS, False)
    now = time.time()
    stale_threshold = max(
        0.1,
        float(getattr(request.app.state, "reader_stale_threshold_sec", 30.0)),
    )
    connectivity: dict[str, bool] = {}
    for seat in SEAT_IDS:
        sv = snapshot.get(_SEAT_COM_SIGNAL.format(seat=seat.upper()))
        timestamp = float(getattr(sv, "timestamp", 0.0)) if sv is not None else 0.0
        is_fresh = timestamp > 0 and (now - timestamp) <= stale_threshold
        connectivity[seat] = bool(sv is not None and is_fresh and sv.value)
    return connectivity


def _expires_at(locks: list[SeatLock]) -> str | None:
    if not locks:
        return None
    return _iso_now(max(lock.expires_at for lock in locks))


def _not_connected_entry(now: float, **extra: object) -> dict:
    return {
        **extra,
        "error": "seat_not_connected",
        "reason": "ECU is not connected or not responding",
        "applied_at": _iso_now(now),
    }


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/catalog", summary="Dev Mode signal families and selectable states")
async def devmode_catalog(request: Request):
    """Metadata the frontend uses to build one tab and its state buttons per signal family."""
    families = []
    for family, spec in SIGNAL_FAMILIES.items():
        families.append(
            {
                "signal_name": family,
                "kind": spec["kind"],
                "states": _family_states(family, spec),
                "signals": [
                    template.format(seat=seat.upper())
                    for seat in SEAT_IDS
                    for template in spec["templates"]
                ],
            }
        )
    return {
        "seats": list(SEAT_IDS),
        "families": families,
        "block_timeout_sec": _default_timeout(),
        "status_stale_timeout_sec": float(
            getattr(request.app.state, "reader_stale_threshold_sec", 30.0)
        ),
    }


@router.get("/status", summary="Current Dev Mode seat locks")
async def devmode_status(request: Request):
    owner = _require_owner(request)
    now = time.time()
    locks = _registry().active_locks(now)
    if locks:
        _ensure_devmode_cleanup_task(request)
    else:
        await _stop_devmode_cleanup_task_if_idle(request)
    connectivity = await _seat_connectivity(request)
    seats = {
        seat: {
            "selected": seat in locks,
            "owned": seat in locks and locks[seat].owner == owner,
            "connected": connectivity.get(seat, True),
            "expires_at": _iso_now(locks[seat].expires_at) if seat in locks else None,
            "remaining_sec": round(locks[seat].remaining_sec(now), 3) if seat in locks else 0.0,
        }
        for seat in SEAT_IDS
    }
    return {"seats": seats, "expires_at": _expires_at(list(locks.values()))}


@router.post("/seats/select", summary="Select seats for Dev Mode (locks other sections out)")
async def select_seats(body: DevModeSeatSelectRequest, request: Request):
    seats = _normalize_seats(body.seats)
    owner = _require_owner(request)
    registry = _registry()
    connectivity = await _seat_connectivity(request)
    now = time.time()

    applied: dict[str, dict] = {}
    granted: list[SeatLock] = []
    for seat, selected in seats.items():
        if not selected:
            registry.release(seat, owner)
            applied[seat] = {"selected": False, "applied_at": _iso_now(now)}
            continue
        if not connectivity.get(seat, True):
            applied[seat] = _not_connected_entry(now, selected=False)
            continue
        try:
            lock = registry.acquire(seat, owner, body.block_timeout_sec, now=now)
        except PermissionError:
            applied[seat] = {
                "selected": False,
                "error": "seat_locked",
                "reason": "Seat is locked by another Dev Mode section",
                "applied_at": _iso_now(now),
            }
            continue
        granted.append(lock)
        applied[seat] = {"selected": True, "applied_at": _iso_now(now)}

    requested_selection = [seat for seat, selected in seats.items() if selected]
    if requested_selection and not granted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"applied": applied, "expires_at": None},
        )

    if registry.active_locks(now):
        _ensure_devmode_cleanup_task(request)
    else:
        await _stop_devmode_cleanup_task_if_idle(request)

    return {
        "applied": applied,
        "expires_at": _expires_at(granted) or _expires_at(list(registry.active_locks(now).values())),
    }


@router.post("/exit", summary="Leave Dev Mode and release all seat locks of this section")
async def exit_devmode(request: Request):
    released = _registry().release_owner(_require_owner(request))
    await _stop_devmode_cleanup_task_if_idle(request)
    return {"released": sorted(released), "released_at": _iso_now()}


@router.post("/signals", summary="Apply one signal family to several seats at once")
async def apply_devmode_signal(body: DevModeSignalRequest, request: Request):
    family = body.signal_name.strip()
    if family not in SIGNAL_FAMILIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported signal '{body.signal_name}'. Supported: {sorted(SIGNAL_FAMILIES)}",
        )
    _validate_value(family, body.value)

    seats = _normalize_seats(body.seats)
    target_seats = [seat for seat, selected in seats.items() if selected]
    if not target_seats:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No seat selected",
        )

    writer = getattr(request.app.state, "writer", None)
    if writer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="CAN writer not available"
        )

    owner = _require_owner(request)
    registry = _registry()
    connectivity = await _seat_connectivity(request)
    now = time.time()

    applied: dict[str, dict] = {}
    granted: list[SeatLock] = []
    writes: dict[str, float] = {}
    seat_of_write: dict[str, str] = {}

    for seat in target_seats:
        if not connectivity.get(seat, True):
            applied[seat] = _not_connected_entry(now, signal_name=family)
            continue
        try:
            granted.append(registry.acquire(seat, owner, body.block_timeout_sec, now=now))
        except PermissionError:
            applied[seat] = {
                "signal_name": family,
                "error": "seat_locked",
                "reason": "Seat is locked by another Dev Mode section",
                "applied_at": _iso_now(now),
            }
            continue
        for name, value in _expand_signals(family, seat, body.value).items():
            writes[name] = value
            seat_of_write[name] = seat

    if writes:
        try:
            sent, errors = await writer.send_signals_batch(writes)
        except can.CanError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc

        failed_seats: dict[str, str] = {}
        for error in errors:
            seat = seat_of_write.get(error.get("signal_name", ""))
            if seat:
                failed_seats.setdefault(seat, error.get("error", "write failed"))

        for seat in target_seats:
            if seat in applied:
                continue
            if seat in failed_seats:
                applied[seat] = {
                    "signal_name": family,
                    "error": "signal_not_available",
                    "reason": failed_seats[seat],
                    "applied_at": _iso_now(now),
                }
                continue
            applied[seat] = {
                "signal_name": family,
                "value": body.value,
                "signals": {
                    name: sent[name] for name in seat_of_write if seat_of_write[name] == seat and name in sent
                },
                "applied_at": _iso_now(now),
            }

    if all("error" in entry for entry in applied.values()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"applied": applied, "expires_at": None},
        )

    if registry.active_locks(now):
        _ensure_devmode_cleanup_task(request)
    else:
        await _stop_devmode_cleanup_task_if_idle(request)

    return {"applied": applied, "expires_at": _expires_at(granted)}
