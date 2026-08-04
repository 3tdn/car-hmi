"""Route REST cho cấu hình hiển thị tín hiệu và ngưỡng cảnh báo theo từng tín hiệu."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status, Body, UploadFile, File
from fastapi.responses import FileResponse

from src.api.models import (
    BackupListResponse,
    CANInfoResponse,
    ConfigReloadStatusResponse,
    DBCGenerateRequest,
    DBCUploadResponse,
    SignalConfigResponse,
    UpdateSignalConfigRequest,
    ProcessorConfigResponse,
    UpdateProcessorConfigRequest,
)
from src.core.config import load_config
from src.core.config_manager import read_config
from src.storage.repository import SignalConfigRecord
import yaml

router = APIRouter()


@router.get("", summary="List all signal configurations")
async def list_signal_configs(request: Request) -> list[SignalConfigResponse]:
    # TODO: tải từ bảng signal_config (Giai đoạn 4)
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
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal '{signal_name}' not found"
        )
    return SignalConfigResponse(signal_name=signal_name, unit=getattr(sv, "unit", None))


@router.patch("/signal/{signal_name}", response_model=SignalConfigResponse, summary="Update signal config")
async def update_signal_config(signal_name: str, body: UpdateSignalConfigRequest, request: Request):
    sv = await request.app.state.store.get(signal_name)
    if sv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Signal '{signal_name}' not found"
        )

    repo = request.app.state.repo
    existing = None
    if repo:
        existing = await repo.get_signal_config(signal_name)

    updates = body.model_dump(exclude_unset=True)

    # Mặc định từ bản ghi DB hiện có nếu có, ngược lại lấy từ signal store
    unit = updates.get("unit", existing.unit if existing else getattr(sv, "unit", None))
    min_value = updates.get("min_value", existing.min_value if existing else None)
    max_value = updates.get("max_value", existing.max_value if existing else None)
    group_name = existing.group_name if existing else None
    widget_type = updates.get("widget_type", existing.widget_type if existing else None)
    writable = updates.get("writable", existing.writable if existing else False)

    # Lưu vào bảng signal_config
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
    manager = request.app.state.config_manager
    try:
        status_info = await manager.patch_general_config(body, runtime=getattr(request.app.state, "runner", None))
        cfg = load_config("config/system.json")
    except (ValueError, OSError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"config": cfg.model_dump(), "reload": status_info}


@router.post("/general/reset", summary="Reset application config to defaults")
async def reset_general_config(request: Request):
    manager = request.app.state.config_manager
    result = await manager.reset_general_config(runtime=getattr(request.app.state, "runner", None))
    return {"ok": result["ok"], "default": result["config"], "reload": result}


@router.get("/alarms", summary="Get alarms config (raw YAML as JSON)")
async def get_alarms_config():
    from src.core.config_manager import read_alarms

    data = read_alarms()
    return data


@router.post("/alarms", summary="Update alarms config (JSON body)")
async def post_alarms_config(body: dict, request: Request):
    manager = request.app.state.config_manager
    try:
        result = await manager.replace_alarms_config(body, runtime=getattr(request.app.state, "runner", None))
    except (ValueError, OSError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"ok": result["ok"], "reload": result}


@router.post("/alarms/reset", summary="Reset alarms config to empty default")
async def reset_alarms_config(request: Request):
    manager = request.app.state.config_manager
    result = await manager.reset_alarms_config(runtime=getattr(request.app.state, "runner", None))
    return {"ok": result["ok"], "written": result["written"], "reload": result}


@router.post("/processor", response_model=ProcessorConfigResponse, summary="Update processor config")
async def update_processor_config_endpoint(request: Request, body: UpdateProcessorConfigRequest = Body(...)) -> ProcessorConfigResponse:
    from src.core.config_manager import update_processor_config
    from src.core.config import load_config

    # Update on-disk JSON first
    try:
        update_processor_config(max_queue_size=body.max_queue_size, queue_policy=body.queue_policy, path="config/system.json")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

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


@router.get("/backups", response_model=BackupListResponse, summary="List config backups")
async def list_backups(request: Request) -> BackupListResponse:
    items = request.app.state.backup_manager.list_backups()
    return BackupListResponse(items=[item.__dict__ for item in items], total=len(items))


@router.post("/backups/create", response_model=dict, summary="Create manual config backup")
async def create_backup(request: Request):
    manager = request.app.state.backup_manager
    record = manager.create_backup("config/system.json", creator="manual")
    return {"ok": True, "backup": record.__dict__}


@router.post("/backups/restore/{backup_id}", response_model=dict, summary="Restore a config backup")
async def restore_backup(backup_id: str, request: Request):
    manager = request.app.state.config_manager
    try:
        result = await manager.restore_backup(backup_id, runtime=getattr(request.app.state, "runner", None))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": result["ok"], "reload": result}


@router.delete("/backups/{backup_id}", response_model=dict, summary="Delete a config backup")
async def delete_backup(backup_id: str, request: Request):
    deleted = request.app.state.backup_manager.delete_backup(backup_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backup '{backup_id}' not found")
    return {"ok": True}


@router.get("/backups/{backup_id}/download", summary="Download a config backup")
async def download_backup(backup_id: str, request: Request):
    record = request.app.state.backup_manager.get_backup(backup_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Backup '{backup_id}' not found")
    return FileResponse(record.file_path, filename=record.file_name, media_type="application/json")


@router.get("/can_info", response_model=CANInfoResponse, summary="Get CAN interface runtime information")
async def get_can_info(request: Request) -> CANInfoResponse:
    interfaces = []
    cfg = read_config("config/system.json")
    for ch in cfg.get("can", []):
        name = ch.get("channel", "unknown")
        state = "DOWN"
        connected = False
        rx_packets = None
        tx_packets = None
        try:
            stats_path = Path("/sys/class/net") / name / "statistics"
            operstate_path = Path("/sys/class/net") / name / "operstate"
            if operstate_path.exists():
                state = operstate_path.read_text(encoding="utf-8").strip().upper()
                connected = state == "UP"
            if stats_path.exists():
                rx_file = stats_path / "rx_packets"
                tx_file = stats_path / "tx_packets"
                if rx_file.exists():
                    rx_packets = int(rx_file.read_text(encoding="utf-8").strip())
                if tx_file.exists():
                    tx_packets = int(tx_file.read_text(encoding="utf-8").strip())
        except Exception:
            pass
        interfaces.append(
            {
                "name": name,
                "connected": connected,
                "state": state,
                "bitrate": ch.get("bitrate"),
                "driver": ch.get("interface"),
                "hardware": "virtual" if ch.get("interface") == "virtual" else "unknown",
                "rx_packets": rx_packets,
                "tx_packets": tx_packets,
                "supported_bitrates": [125000, 250000, 500000, 1000000],
            }
        )
    return CANInfoResponse(interfaces=interfaces)


@router.post("/dbc/upload", response_model=DBCUploadResponse, summary="Upload and parse a DBC file")
async def upload_dbc(request: Request, file: UploadFile = File(...)) -> DBCUploadResponse:
    job = request.app.state.dbc_job_manager.create_job(file.filename or "upload.dbc", await file.read())
    return DBCUploadResponse(
        id=job["id"],
        dbc_name=job["dbc_name"],
        created_at=job["created_at"],
        messages=job["messages"],
        unsupported=job["unsupported"],
        errors=job["errors"],
        warnings=job["warnings"],
    )


@router.get("/dbc/parse_result/{job_id}", response_model=dict, summary="Get DBC parse result")
async def get_dbc_parse_result(job_id: str, request: Request):
    try:
        return request.app.state.dbc_job_manager.get_job(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/dbc/generate_config", response_model=dict, summary="Generate can json from parsed DBC job")
async def generate_dbc_config(body: DBCGenerateRequest, request: Request):
    try:
        result = request.app.state.dbc_job_manager.generate_config(body.id, body.output_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"ok": True, "result": result}
