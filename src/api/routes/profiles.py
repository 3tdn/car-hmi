"""Route quản lý profiles — tập hợp signal mỗi user muốn hiển thị.

Profiles lưu tại config/profiles.json, còn client session lưu riêng trong data/profile_sessions.json
(tạo tự động nếu chưa có).

Optimistic locking: mỗi PUT yêu cầu truyền section_id đúng với server,
tránh ghi đè đồng thời từ nhiều tab/user. Nếu mismatch → HTTP 409.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.api.models import (
    ActiveProfileResponse,
    ClientProfileSession,
    ProfileCreate,
    ProfileHeartbeatResponse,
    ProfileResponse,
    ProfileSessionsResponse,
    ProfileSetActiveRequest,
    ProfilesResponse,
    ProfileUpdate,
)

router = APIRouter()


def _load_profile_runtime_settings() -> tuple[Path, Path, list[str], int, int, bool]:
    """Đọc runtime settings cho profile/session từ config/system.json."""
    fallback_path = Path("config/profiles.json")
    fallback_sessions_path = Path("data/profile_sessions.json")
    fallback_permission = ["read"]
    fallback_ttl = 600
    fallback_limit = 50
    fallback_allow_legacy = False

    try:
        from src.core.config_manager import read_config

        cfg = read_config().get("profiles", {})
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    path_value = cfg.get("profiles_path", str(fallback_path))
    profiles_path = Path(path_value) if path_value else fallback_path

    sessions_value = cfg.get("sessions_path", str(fallback_sessions_path))
    sessions_path = Path(sessions_value) if sessions_value else fallback_sessions_path

    permission_raw = cfg.get("default_profile_permission", fallback_permission)
    if isinstance(permission_raw, list):
        permission = [str(item) for item in permission_raw if str(item).strip()]
    else:
        permission = []
    if not permission:
        permission = fallback_permission

    try:
        ttl_seconds = int(cfg.get("session_online_ttl_seconds", fallback_ttl))
    except (TypeError, ValueError):
        ttl_seconds = fallback_ttl
    ttl_seconds = max(1, ttl_seconds)

    try:
        history_limit = int(cfg.get("session_history_limit", fallback_limit))
    except (TypeError, ValueError):
        history_limit = fallback_limit
    history_limit = max(1, history_limit)

    allow_legacy = bool(cfg.get("allow_legacy_profile_mutations", fallback_allow_legacy))

    return profiles_path, sessions_path, permission, ttl_seconds, history_limit, allow_legacy


PROFILES_PATH, PROFILE_SESSIONS_PATH, DEFAULT_PROFILE_PERMISSION, SESSION_ONLINE_TTL_SECONDS, SESSION_HISTORY_LIMIT, ALLOW_LEGACY_PROFILE_MUTATIONS = _load_profile_runtime_settings()
PROFILE_HEADER = "X-Profile-Name"
DEV_MODE_HEADER = "X-Dev-Mode"
CLIENT_ID_HEADER = "X-Client-Id"
PermissionScope = Literal["read", "write", "full"]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_profiles() -> dict[str, Any]:
    if not PROFILES_PATH.exists():
        return {"active": None, "profiles": {}}
    try:
        data = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active": None, "profiles": {}}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("profiles"), dict):
        data["profiles"] = {}
    return data


def _load_client_sessions() -> dict[str, dict[str, Any]]:
    if not PROFILE_SESSIONS_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILE_SESSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(data, dict):
        sessions = data.get("client_sessions", data)
        if isinstance(sessions, dict):
            return sessions
    return {}


def _save_profiles(data: dict[str, Any]) -> None:
    from src.core.config_manager import write_config

    write_config(data, PROFILES_PATH)


def _save_client_sessions(sessions: dict[str, dict[str, Any]]) -> None:
    from src.core.config_manager import write_config

    write_config({"client_sessions": sessions}, PROFILE_SESSIONS_PATH)


def _load() -> dict[str, Any]:
    data = _load_profiles()
    legacy_sessions = data.pop("client_sessions", None)
    sessions = _load_client_sessions()
    if isinstance(legacy_sessions, dict):
        if not sessions:
            sessions = legacy_sessions
            _save_client_sessions(sessions)
        _save_profiles(data)
    elif legacy_sessions is not None:
        _save_profiles(data)

    data["client_sessions"] = sessions
    return data


def _save(data: dict[str, Any]) -> None:
    profiles = dict(data)
    sessions = profiles.pop("client_sessions", {})
    if not isinstance(sessions, dict):
        sessions = {}
    _save_profiles(profiles)
    _save_client_sessions(sessions)


def _section_id(profile: dict) -> str:
    """MD5 hash (12 chars) của nội dung profile — dùng làm optimistic lock token."""
    blob = json.dumps(profile, sort_keys=True).encode()
    return hashlib.md5(blob).hexdigest()[:12]  # noqa: S324 — non-crypto use


def _to_response(name: str, p: dict) -> ProfileResponse:
    return ProfileResponse(
        name=name,
        signals=p.get("signals", []),
        permission=p.get("permission", DEFAULT_PROFILE_PERMISSION),
        description=p.get("description"),
        section_id=_section_id(p),
    )


def _normalized_client_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    client_id = raw.strip()
    if not client_id:
        return None
    return client_id[:128]


def _client_sessions(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sessions = data.setdefault("client_sessions", {})
    if not isinstance(sessions, dict):
        data["client_sessions"] = {}
        return data["client_sessions"]
    return sessions


def _resolve_target_profile(
    data: dict[str, Any],
    *,
    profile_name: str | None,
    client_id: str | None,
) -> str | None:
    if profile_name:
        return profile_name

    if client_id:
        client_session = _client_sessions(data).get(client_id)
        session_active = client_session.get("active") if isinstance(client_session, dict) else None
        if session_active:
            return session_active

    return data.get("active")


def _session_last_seen(state: dict[str, Any]) -> float:
    raw = state.get("last_seen", state.get("updated_at", 0))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _session_is_online(state: dict[str, Any], *, now: float, ttl_seconds: int | None = None) -> bool:
    if ttl_seconds is None:
        ttl_seconds = SESSION_ONLINE_TTL_SECONDS
    return (now - _session_last_seen(state)) <= ttl_seconds


def _cleanup_orphan_sessions(data: dict[str, Any]) -> bool:
    profiles = data.get("profiles", {})
    sessions = _client_sessions(data)
    dangling = [
        client_id
        for client_id, state in sessions.items()
        if not isinstance(state, dict) or state.get("active") not in profiles
    ]
    for client_id in dangling:
        sessions.pop(client_id, None)
    return bool(dangling)


def _trim_sessions_over_capacity(data: dict[str, Any], *, now: float | None = None) -> bool:
    """Giữ lịch sử session tối đa theo ưu tiên mới nhất; chỉ xóa session offline ngoài top limit."""
    timestamp = now if now is not None else time.time()
    sessions = _client_sessions(data)
    if len(sessions) <= SESSION_HISTORY_LIMIT:
        return False

    ranked = sorted(
        (
            (client_id, state)
            for client_id, state in sessions.items()
            if isinstance(state, dict)
        ),
        key=lambda item: _session_last_seen(item[1]),
        reverse=True,
    )
    overflow = ranked[SESSION_HISTORY_LIMIT:]
    removed = False
    for client_id, state in overflow:
        if not _session_is_online(state, now=timestamp):
            sessions.pop(client_id, None)
            removed = True
    return removed


def _cleanup_sessions(data: dict[str, Any], *, now: float | None = None) -> bool:
    removed_orphans = _cleanup_orphan_sessions(data)
    removed_overflow = _trim_sessions_over_capacity(data, now=now)
    return removed_orphans or removed_overflow


def _touch_client_session(data: dict[str, Any], client_id: str, *, active: str | None = None, now: float | None = None) -> dict[str, Any]:
    timestamp = now if now is not None else time.time()
    sessions = _client_sessions(data)
    state = sessions.get(client_id)
    if not isinstance(state, dict):
        state = {}

    if active is not None:
        state["active"] = active
        state["updated_at"] = timestamp
    else:
        state.setdefault("updated_at", timestamp)

    state["last_seen"] = timestamp
    sessions[client_id] = state
    return state


def _is_dev_mode(request: Request) -> bool:
    return str(request.headers.get(DEV_MODE_HEADER, "")).strip().lower() in {"1", "true", "yes", "on"}


# ── BEGIN LEGACY COMPAT — Xoá sau khi tất cả frontend đã cập nhật sang profile API mới ──────────


def _is_legacy_request(request: Request) -> bool:
    """Trả True nếu request không gửi X-Profile-Name và X-Client-Id.

    Đây là dấu hiệu của frontend cũ (trước khi có profile permission system).
    Khi đó, các thao tác mutate được cho phép chỉ cần X-API-Key (như API cũ).
    """
    return (
        not request.headers.get(PROFILE_HEADER)
        and not request.headers.get(CLIENT_ID_HEADER)
    )


# ── END LEGACY COMPAT ─────────────────────────────────────────────────────────────────────────────


def build_access_warning(
    code: str,
    message: str,
    *,
    profile_name: str | None = None,
    required_permission: PermissionScope | None = None,
    signal_name: str | None = None,
    signals: list[str] | None = None,
) -> dict[str, Any]:
    warning: dict[str, Any] = {
        "code": code,
        "message": message,
        "signals": signals or [],
    }
    if profile_name is not None:
        warning["profile_name"] = profile_name
    if required_permission is not None:
        warning["required_permission"] = required_permission
    if signal_name is not None:
        warning["signal_name"] = signal_name
    return warning


def get_profile_context(
    profile_name: str | None = None,
    *,
    client_id: str | None = None,
    allow_bootstrap: bool = False,
) -> tuple[str | None, dict[str, Any] | None, dict[str, Any]]:
    data = _load()
    if _cleanup_sessions(data):
        _save(data)
    client_id = _normalized_client_id(client_id)
    profiles = data.get("profiles", {})

    if allow_bootstrap and not profiles:
        return None, None, data

    target = _resolve_target_profile(data, profile_name=profile_name, client_id=client_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=build_access_warning(
                "profile_not_selected",
                "No profile selected for this operation",
            ),
        )

    profile = profiles.get(target)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=build_access_warning(
                "profile_not_found",
                f"Profile '{target}' không tìm thấy",
                profile_name=target,
            ),
        )

    return target, profile, data


def profile_has_permission(profile: dict[str, Any], required: PermissionScope) -> bool:
    permissions = set(profile.get("permission", DEFAULT_PROFILE_PERMISSION))
    if "full" in permissions:
        return True
    return required in permissions


def profile_allows_signal(
    profile: dict[str, Any],
    signal_name: str,
    alternates: list[str] | tuple[str, ...] | None = None,
) -> bool:
    allowed_signals = set(profile.get("signals", []))
    if not allowed_signals:
        return False
    candidates = {signal_name}
    if alternates:
        candidates.update(name for name in alternates if name)
    return bool(allowed_signals.intersection(candidates))


def require_profile_permission(
    request: Request,
    required: PermissionScope,
    *,
    signal_name: str | None = None,
    alternates: list[str] | tuple[str, ...] | None = None,
    allow_bootstrap: bool = False,
) -> tuple[str | None, dict[str, Any] | None]:
    """Đảm bảo request có profile đủ quyền cho thao tác ghi."""
    profile_name = request.headers.get(PROFILE_HEADER)
    client_id = _normalized_client_id(request.headers.get(CLIENT_ID_HEADER))
    resolved_name, profile, _ = get_profile_context(
        profile_name,
        client_id=client_id,
        allow_bootstrap=allow_bootstrap,
    )

    if allow_bootstrap and profile is None:
        return None, None

    if not profile_has_permission(profile, required):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=build_access_warning(
                "profile_permission_denied",
                f"Profile '{resolved_name}' lacks '{required}' permission",
                profile_name=resolved_name,
                required_permission=required,
                signal_name=signal_name,
            ),
        )

    if signal_name is not None and not profile_allows_signal(profile, signal_name, alternates):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=build_access_warning(
                "profile_signal_denied",
                f"Signal '{signal_name}' is outside profile '{resolved_name}' scope",
                profile_name=resolved_name,
                required_permission=required,
                signal_name=signal_name,
            ),
        )

    return resolved_name, profile


def _require_profile_mutation_permission(request: Request, *, allow_bootstrap: bool = False) -> None:
    if _is_dev_mode(request):
        return

    if _is_legacy_request(request):
        if ALLOW_LEGACY_PROFILE_MUTATIONS:
            return
        if allow_bootstrap:
            data = _load()
            if not data.get("profiles"):
                return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=build_access_warning(
                "profile_headers_required",
                f"Legacy profile mutation is disabled; send '{PROFILE_HEADER}' (or '{CLIENT_ID_HEADER}')",
            ),
        )

    require_profile_permission(request, "full", allow_bootstrap=allow_bootstrap)


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/profiles",
    response_model=ProfilesResponse,
    summary="List all profiles",
)
async def list_profiles(request: Request):
    """Trả về danh sách tất cả profiles và tên profile đang active."""
    data = _load()
    if _cleanup_sessions(data):
        _save(data)
    client_id = _normalized_client_id(request.headers.get(CLIENT_ID_HEADER))
    resolved_active = _resolve_target_profile(data, profile_name=None, client_id=client_id)
    profiles = [_to_response(n, p) for n, p in data.get("profiles", {}).items()]
    return ProfilesResponse(
        profiles=profiles,
        total=len(profiles),
        active=resolved_active,
        global_active=data.get("active"),
        client_id=client_id,
    )


@router.get(
    "/profile/sessions",
    response_model=ProfileSessionsResponse,
    summary="List client active-profile sessions",
)
async def list_profile_sessions(request: Request):
    """Trả về danh sách client đang map tới active profile nào."""
    if not _is_dev_mode(request):
        require_profile_permission(request, "read")

    data = _load()
    now = time.time()
    if _cleanup_sessions(data, now=now):
        _save(data)
    sessions = _client_sessions(data)

    items = [
        ClientProfileSession(
            client_id=client_id,
            active=state.get("active"),
            updated_at=float(state.get("updated_at", 0)),
            last_seen=_session_last_seen(state),
            status="online" if _session_is_online(state, now=now) else "offline",
        )
        for client_id, state in sessions.items()
        if isinstance(state, dict) and state.get("active")
    ]
    items.sort(key=lambda item: item.updated_at, reverse=True)
    return ProfileSessionsResponse(
        sessions=items,
        total=len(items),
        global_active=data.get("active"),
        ttl_seconds=SESSION_ONLINE_TTL_SECONDS,
        server_time=now,
    )


@router.post(
    "/profile/heartbeat",
    response_model=ProfileHeartbeatResponse,
    summary="Heartbeat for client profile session",
)
async def profile_heartbeat(request: Request):
    """Cập nhật heartbeat để giữ session client sống và phục vụ trạng thái online/offline."""
    client_id = _normalized_client_id(request.headers.get(CLIENT_ID_HEADER))
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=build_access_warning(
                "client_id_required",
                f"Header '{CLIENT_ID_HEADER}' is required for heartbeat",
            ),
        )

    data = _load()
    now = time.time()
    if _cleanup_sessions(data, now=now):
        _save(data)

    active = _resolve_target_profile(data, profile_name=None, client_id=client_id)
    state = _touch_client_session(data, client_id, active=active, now=now)
    _save(data)
    return ProfileHeartbeatResponse(
        client_id=client_id,
        active=state.get("active"),
        last_seen=_session_last_seen(state),
        ttl_seconds=SESSION_ONLINE_TTL_SECONDS,
    )


@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Get profile by name (or active profile)",
)
async def get_profile(request: Request, name: str | None = Query(None, description="Tên profile; bỏ trống để lấy active profile")):
    """Lấy một profile theo tên, hoặc profile đang active nếu không truyền name."""
    data = _load()
    if _cleanup_sessions(data):
        _save(data)
    client_id = _normalized_client_id(request.headers.get(CLIENT_ID_HEADER))
    target = _resolve_target_profile(data, profile_name=name, client_id=client_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=build_access_warning(
                "profile_not_selected",
                "Không có active profile",
            ),
        )
    p = data.get("profiles", {}).get(target)
    if p is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=build_access_warning(
                "profile_not_found",
                f"Profile '{target}' không tìm thấy",
                profile_name=target,
            ),
        )
    return _to_response(target, p)


@router.put(
    "/profile/active",
    response_model=ActiveProfileResponse,
    summary="Set active profile",
)
async def set_active_profile(body: ProfileSetActiveRequest, request: Request):
    """Đổi profile active trên server để các client khác cùng nhìn thấy trạng thái mới."""
    _require_profile_mutation_permission(request)

    data = _load()
    if _cleanup_sessions(data):
        _save(data)
    profiles = data.get("profiles", {})
    target = body.name
    client_id = _normalized_client_id(request.headers.get(CLIENT_ID_HEADER))

    current_active = _resolve_target_profile(data, profile_name=None, client_id=client_id)

    if target not in profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=build_access_warning(
                "profile_not_found",
                f"Profile '{target}' không tìm thấy",
                profile_name=target,
            ),
        )

    if current_active == target:
        if client_id:
            _touch_client_session(data, client_id, active=target)
            _save(data)
        return ActiveProfileResponse(
            active=target,
            global_active=data.get("active"),
            client_id=client_id,
            warnings=[
                build_access_warning(
                    "profile_already_active",
                    f"Profile '{target}' đã là active profile",
                    profile_name=target,
                )
            ],
        )

    if client_id:
        _touch_client_session(data, client_id, active=target)
    else:
        data["active"] = target

    _save(data)
    return ActiveProfileResponse(active=target, global_active=data.get("active"), client_id=client_id)


@router.post(
    "/profile",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new profile",
)
async def create_profile(body: ProfileCreate, request: Request):
    """Tạo profile mới. Profile đầu tiên sẽ được đặt làm active tự động."""
    _require_profile_mutation_permission(request, allow_bootstrap=True)
    data = _load()
    profiles = data.setdefault("profiles", {})
    if body.name in profiles:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=build_access_warning(
                "profile_already_exists",
                f"Profile '{body.name}' đã tồn tại",
                profile_name=body.name,
            ),
        )
    p: dict[str, Any] = {
        "signals": body.signals,
        "permission": body.permission,
        "description": body.description,
        "created_at": time.time(),
    }
    profiles[body.name] = p
    if data.get("active") is None:
        data["active"] = body.name
    _save(data)
    return _to_response(body.name, p)


@router.put(
    "/profile",
    response_model=ProfileResponse,
    summary="Update profile (optimistic lock)",
)
async def update_profile(body: ProfileUpdate, request: Request):
    """Cập nhật profile. Yêu cầu section_id đúng với server để tránh xung đột đồng thời.

    Nếu section_id không khớp → HTTP 409 → client cần GET lại và thử lại.
    """
    _require_profile_mutation_permission(request)
    data = _load()
    profiles = data.get("profiles", {})
    if body.name not in profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=build_access_warning(
                "profile_not_found",
                f"Profile '{body.name}' không tìm thấy",
                profile_name=body.name,
            ),
        )
    p = profiles[body.name]
    if _section_id(p) != body.section_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=build_access_warning(
                "profile_section_mismatch",
                "section_id không khớp — vui lòng GET lại profile và thử lại",
                profile_name=body.name,
            ),
        )
    p["signals"] = body.signals
    # BEGIN LEGACY COMPAT: nếu frontend cũ không gửi permission, giữ nguyên giá trị cũ.
    if body.permission is not None:
        p["permission"] = body.permission
    # END LEGACY COMPAT
    p["description"] = body.description
    _save(data)
    return _to_response(body.name, p)


@router.delete(
    "/profile/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete profile",
)
async def delete_profile(name: str, request: Request):
    """Xóa profile theo tên. Nếu là active profile, active sẽ chuyển sang profile tiếp theo."""
    _require_profile_mutation_permission(request)
    data = _load()
    if name not in data.get("profiles", {}):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=build_access_warning(
                "profile_not_found",
                f"Profile '{name}' không tìm thấy",
                profile_name=name,
            ),
        )
    del data["profiles"][name]
    if data.get("active") == name:
        remaining = list(data["profiles"].keys())
        data["active"] = remaining[0] if remaining else None
    sessions = _client_sessions(data)
    for client_id in [cid for cid, state in sessions.items() if isinstance(state, dict) and state.get("active") == name]:
        sessions.pop(client_id, None)
    _save(data)
