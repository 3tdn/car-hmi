"""Route quản lý profiles — tập hợp signal mỗi user muốn hiển thị.

Dữ liệu lưu tại config/profiles.json (tạo tự động nếu chưa có).

Optimistic locking: mỗi PUT yêu cầu truyền section_id đúng với server,
tránh ghi đè đồng thời từ nhiều tab/user. Nếu mismatch → HTTP 409.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from src.api.models import ProfileCreate, ProfileResponse, ProfilesResponse, ProfileUpdate

router = APIRouter()

PROFILES_PATH = Path("config/profiles.json")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load() -> dict[str, Any]:
    if not PROFILES_PATH.exists():
        return {"active": None, "profiles": {}}
    return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))


def _save(data: dict[str, Any]) -> None:
    from src.core.config_manager import write_config
    write_config(data, PROFILES_PATH)


def _section_id(profile: dict) -> str:
    """MD5 hash (12 chars) của nội dung profile — dùng làm optimistic lock token."""
    blob = json.dumps(profile, sort_keys=True).encode()
    return hashlib.md5(blob).hexdigest()[:12]  # noqa: S324 — non-crypto use


def _to_response(name: str, p: dict) -> ProfileResponse:
    return ProfileResponse(
        name=name,
        signals=p.get("signals", []),
        description=p.get("description"),
        section_id=_section_id(p),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/profiles",
    response_model=ProfilesResponse,
    summary="List all profiles",
)
async def list_profiles():
    """Trả về danh sách tất cả profiles và tên profile đang active."""
    data = _load()
    profiles = [_to_response(n, p) for n, p in data.get("profiles", {}).items()]
    return ProfilesResponse(profiles=profiles, total=len(profiles), active=data.get("active"))


@router.get(
    "/profile",
    response_model=ProfileResponse,
    summary="Get profile by name (or active profile)",
)
async def get_profile(name: str | None = Query(None, description="Tên profile; bỏ trống để lấy active profile")):
    """Lấy một profile theo tên, hoặc profile đang active nếu không truyền name."""
    data = _load()
    target = name or data.get("active")
    if not target:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không có active profile")
    p = data.get("profiles", {}).get(target)
    if p is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Profile '{target}' không tìm thấy")
    return _to_response(target, p)


@router.post(
    "/profile",
    response_model=ProfileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create new profile",
)
async def create_profile(body: ProfileCreate):
    """Tạo profile mới. Profile đầu tiên sẽ được đặt làm active tự động."""
    data = _load()
    profiles = data.setdefault("profiles", {})
    if body.name in profiles:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile '{body.name}' đã tồn tại",
        )
    p: dict[str, Any] = {
        "signals": body.signals,
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
async def update_profile(body: ProfileUpdate):
    """Cập nhật profile. Yêu cầu section_id đúng với server để tránh xung đột đồng thời.

    Nếu section_id không khớp → HTTP 409 → client cần GET lại và thử lại.
    """
    data = _load()
    profiles = data.get("profiles", {})
    if body.name not in profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{body.name}' không tìm thấy",
        )
    p = profiles[body.name]
    if _section_id(p) != body.section_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="section_id không khớp — vui lòng GET lại profile và thử lại",
        )
    p["signals"] = body.signals
    p["description"] = body.description
    _save(data)
    return _to_response(body.name, p)


@router.delete(
    "/profile/{name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete profile",
)
async def delete_profile(name: str):
    """Xóa profile theo tên. Nếu là active profile, active sẽ chuyển sang profile tiếp theo."""
    data = _load()
    if name not in data.get("profiles", {}):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Profile '{name}' không tìm thấy",
        )
    del data["profiles"][name]
    if data.get("active") == name:
        remaining = list(data["profiles"].keys())
        data["active"] = remaining[0] if remaining else None
    _save(data)
