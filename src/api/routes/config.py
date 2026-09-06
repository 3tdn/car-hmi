"""REST routes for per-signal display configuration and alarm thresholds."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status, Body

from src.api.models import (
    SignalConfigResponse,
    UpdateSignalConfigRequest,
    ProcessorConfigResponse,
    UpdateProcessorConfigRequest,
)
from src.api.routes.profiles import build_access_warning, require_profile_permission
from src.storage.repository import SignalConfigRecord
import yaml

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
    "/signal/{signal_name}", response_model=SignalConfigResponse, summary="Get config for one signal"
)
async def get_signal_config(signal_name: str, request: Request):
    sv = await request.app.state.store.get(signal_name)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_config_error("signal_config_not_found", f"Signal '{signal_name}' not found", signal_name=signal_name),
        )
    return SignalConfigResponse(signal_name=signal_name, unit=getattr(sv, "unit", None))


@router.patch("/signal/{signal_name}", response_model=SignalConfigResponse, summary="Update signal config")
async def update_signal_config(signal_name: str, body: UpdateSignalConfigRequest, request: Request):
    require_profile_permission(request, "full")
    sv = await request.app.state.store.get(signal_name)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_config_error("signal_config_not_found", f"Signal '{signal_name}' not found", signal_name=signal_name),
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


@router.get("/processor", response_model=ProcessorConfigResponse, summary="Get processor config")
async def get_processor_config() -> ProcessorConfigResponse:
    from src.core.config import load_config

    cfg = load_config("config/system.json")
    return ProcessorConfigResponse(max_queue_size=cfg.processor.max_queue_size, queue_policy=cfg.processor.queue_policy)


@router.get("/general", summary="Get full application config")
async def get_general_config():
    from src.core.config import load_config

    cfg = load_config("config/system.json")
    return cfg.model_dump()


@router.patch("/general", summary="Patch application config (partial)")
async def patch_general_config(body: dict, request: Request):
    require_profile_permission(request, "full")
    from src.core.config_manager import update_config_partial

    try:
        update_config_partial(body, path="config/system.json")
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_config_error("general_config_patch_invalid", str(exc)),
        ) from exc
    # try to reload validated config
    from src.core.config import load_config

    try:
        cfg = load_config("config/system.json")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_config_error("general_config_validation_failed", str(exc)),
        ) from exc
    return cfg.model_dump()


@router.post("/general/reset", summary="Reset application config to defaults")
async def reset_general_config(request: Request):
    require_profile_permission(request, "full")
    from src.core.config_manager import write_default_bus

    default = write_default_bus(path="config/system.json")
    return {"ok": True, "default": default}


@router.get("/alarms", summary="Get alarms config (raw YAML as JSON)")
async def get_alarms_config():
    from src.core.config_manager import read_alarms

    data = read_alarms()
    return data


@router.post("/alarms", summary="Update alarms config (JSON body)")
async def post_alarms_config(body: dict, request: Request):
    require_profile_permission(request, "full")
    from src.core.config_manager import write_alarms

    try:
        write_alarms(body)
    except (ValueError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_config_error("alarms_config_update_failed", str(exc)),
        ) from exc
    return {"ok": True}


@router.post("/alarms/reset", summary="Reset alarms config to empty default")
async def reset_alarms_config(request: Request):
    require_profile_permission(request, "full")
    from src.core.config_manager import write_default_alarms

    written = write_default_alarms(path="config/alarms.json")
    return {"ok": True, "written": written}


@router.post("/processor", response_model=ProcessorConfigResponse, summary="Update processor config")
async def update_processor_config_endpoint(request: Request, body: UpdateProcessorConfigRequest = Body(...)) -> ProcessorConfigResponse:
    require_profile_permission(request, "full")
    from src.core.config_manager import update_processor_config
    from src.core.config import load_config

    # Update on-disk JSON first
    try:
        update_processor_config(max_queue_size=body.max_queue_size, queue_policy=body.queue_policy, path="config/system.json")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_config_error("processor_config_invalid", str(exc)),
        ) from exc

    # Try to apply to running components if available
    readers = getattr(request.app.state, "readers", None)
    pipeline = getattr(request.app.state, "pipeline", None)
    if readers and body.queue_policy is not None:
        for reader in readers:
            try:
                reader.set_queue_policy(body.queue_policy)
            except Exception:
                pass
    # Attempt a best-effort migration of the runtime RX queue via the runner
    runner = getattr(request.app.state, "runner", None)
    if runner and body.max_queue_size is not None:
        try:
            # migrate_rx_queue will route new frames and drain the old queue into the new one
            await runner.migrate_rx_queue(int(body.max_queue_size), timeout=5.0)
        except Exception:
            pass

    cfg = load_config("config/system.json")
    return ProcessorConfigResponse(max_queue_size=cfg.processor.max_queue_size, queue_policy=cfg.processor.queue_policy)
