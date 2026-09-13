"""REST routes for runtime and per-signal display configuration."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from src.api.models import (
    ProcessorConfigResponse,
    SignalConfigResponse,
    UpdateProcessorConfigRequest,
    UpdateSignalConfigRequest,
)
from src.api.routes.profiles import build_access_warning, require_profile_permission
from src.core.config_manager import ConfigUpdateError
from src.storage.repository import SignalConfigRecord

router = APIRouter()


def _config_error(code: str, message: str, *, signal_name: str | None = None) -> dict:
    return build_access_warning(code, message, signal_name=signal_name)


@router.get("", summary="List all signal configurations")
async def list_signal_configs(request: Request) -> list[SignalConfigResponse]:
    # TODO: load from the signal_config table (Phase 4)
    cfg = request.app.state.store
    snapshot = await cfg.get_snapshot()
    return [
        SignalConfigResponse(signal_name=name, unit=getattr(sv, "unit", None))
        for name, sv in snapshot.items()
    ]


@router.get(
    "/signal/{signal_name}",
    response_model=SignalConfigResponse,
    summary="Get config for one signal",
)
async def get_signal_config(signal_name: str, request: Request):
    sv = await request.app.state.store.get(signal_name)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_config_error(
                "signal_config_not_found",
                f"Signal '{signal_name}' not found",
                signal_name=signal_name,
            ),
        )
    return SignalConfigResponse(signal_name=signal_name, unit=getattr(sv, "unit", None))


@router.patch(
    "/signal/{signal_name}", response_model=SignalConfigResponse, summary="Update signal config"
)
async def update_signal_config(signal_name: str, body: UpdateSignalConfigRequest, request: Request):
    require_profile_permission(request, "full")
    sv = await request.app.state.store.get(signal_name)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_config_error(
                "signal_config_not_found",
                f"Signal '{signal_name}' not found",
                signal_name=signal_name,
            ),
        )

    repo = request.app.state.repo
    existing = None
    if repo:
        existing = await repo.get_signal_config(signal_name)

    updates = body.model_dump(exclude_unset=True)

    # Default from the existing DB record if available; otherwise read from the signal store
    unit = updates.get("unit", existing.unit if existing else getattr(sv, "unit", None))
    min_value = updates.get("min_value", existing.min_value if existing else None)
    max_value = updates.get("max_value", existing.max_value if existing else None)
    group_name = existing.group_name if existing else None
    widget_type = updates.get("widget_type", existing.widget_type if existing else None)
    writable = updates.get("writable", existing.writable if existing else False)

    # Save to the signal_config table
    record = SignalConfigRecord(
        signal_name=signal_name,
        unit=unit,
        min_value=min_value,
        max_value=max_value,
        group_name=group_name,
        widget_type=widget_type,
        writable=writable,
    )
    if repo:
        await repo.upsert_signal_config(record)

    return SignalConfigResponse(
        signal_name=signal_name,
        unit=unit,
        min_value=min_value,
        max_value=max_value,
        group_name=group_name,
        widget_type=widget_type,
        writable=writable,
    )


# ----- Processor runtime config endpoints -----


def _manager(request: Request):
    return request.app.state.system_config_manager


def _raise_config_error(exc: ConfigUpdateError) -> None:
    if exc.code == "system_config_backup_not_found":
        code = status.HTTP_404_NOT_FOUND
    elif exc.code == "system_config_runtime_apply_failed":
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    else:
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    raise HTTPException(status_code=code, detail=_config_error(exc.code, str(exc))) from exc


@router.get("/processor", response_model=ProcessorConfigResponse, summary="Get processor config")
async def get_processor_config(request: Request) -> ProcessorConfigResponse:
    cfg = _manager(request).read()["processor"]
    return ProcessorConfigResponse(
        max_queue_size=cfg["max_queue_size"], queue_policy=cfg["queue_policy"]
    )


@router.get("/system", summary="Get system config and field update policy")
async def get_system_config(request: Request):
    snapshot = _manager(request).public_snapshot()
    runner = getattr(request.app.state, "runner", None)
    pending = sorted(getattr(runner, "pending_reboot_paths", []))
    snapshot["pending_reboot_paths"] = pending
    snapshot["reboot_required"] = bool(pending)
    return snapshot


@router.get("/system/backups", summary="List fixed-path system config backups")
async def list_system_config_backups(request: Request):
    return {"backups": _manager(request).list_backups()}


@router.post(
    "/system/backups", status_code=status.HTTP_201_CREATED, summary="Back up system config"
)
async def backup_system_config(request: Request):
    require_profile_permission(request, "full")
    try:
        return {"ok": True, "backup": _manager(request).create_backup("manual")}
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.post("/system/backups/{backup_id}/restore", summary="Restore a system config backup")
async def restore_system_config_backup(backup_id: str, request: Request):
    require_profile_permission(request, "full")
    try:
        return await _manager(request).restore(
            backup_id, getattr(request.app.state, "runner", None)
        )
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.patch("/system", summary="Patch system config without dropping unrelated fields")
async def patch_system_config(body: dict, request: Request):
    require_profile_permission(request, "full")
    try:
        return await _manager(request).patch(body, getattr(request.app.state, "runner", None))
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.post("/system/reset", summary="Reset system config from the fixed project template")
async def reset_system_config(request: Request):
    require_profile_permission(request, "full")
    try:
        return await _manager(request).reset(getattr(request.app.state, "runner", None))
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.post("/system/reload", summary="Re-apply live fields from the system config file")
async def reload_system_config(request: Request):
    require_profile_permission(request, "full")
    try:
        return await _manager(request).reload_runtime(getattr(request.app.state, "runner", None))
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.get("/general", summary="Get full application config")
async def get_general_config(request: Request):
    return _manager(request).public_snapshot()["config"]


@router.patch("/general", summary="Patch application config (partial)")
async def patch_general_config(body: dict, request: Request):
    require_profile_permission(request, "full")
    try:
        result = await _manager(request).patch(
            body, getattr(request.app.state, "runner", None)
        )
        return result["config"]
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.post("/general/reset", summary="Reset application config to defaults")
async def reset_general_config(request: Request):
    require_profile_permission(request, "full")
    try:
        result = await _manager(request).reset(getattr(request.app.state, "runner", None))
        return {"ok": True, "default": result["config"]}
    except ConfigUpdateError as exc:
        _raise_config_error(exc)


@router.post(
    "/processor", response_model=ProcessorConfigResponse, summary="Update processor config"
)
async def update_processor_config_endpoint(
    request: Request, body: UpdateProcessorConfigRequest
) -> ProcessorConfigResponse:
    require_profile_permission(request, "full")
    update = dict(body.model_dump(exclude_none=True).items())
    try:
        result = await _manager(request).patch(
            {"processor": update}, getattr(request.app.state, "runner", None)
        )
    except ConfigUpdateError as exc:
        _raise_config_error(exc)
    cfg = result["config"]["processor"]
    return ProcessorConfigResponse(
        max_queue_size=cfg["max_queue_size"], queue_policy=cfg["queue_policy"]
    )
