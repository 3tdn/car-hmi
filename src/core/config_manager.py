"""Config persistence, backup, reload, and DBC generation helpers."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.config import AppConfig, CANConfig, load_config

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("config/system.json")
DEFAULT_ALARMS_PATH = Path("config/alarms.json")
DEFAULT_BACKUP_DIR = Path("config/backups")
DEFAULT_DBC_WORK_DIR = Path("tmp/dbc_jobs")
_VALID_QUEUE_POLICIES = {"drop_oldest", "reject"}


def read_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8")) or {}


def read_alarms(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_ALARMS_PATH
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")) or {}


def write_config(data: dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    _write_json_atomic(p, data)


def write_alarms(data: dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_ALARMS_PATH
    _validate_alarms_payload(data)
    _write_json_atomic(p, data)


def validate_config(path: str | Path | None = None) -> AppConfig:
    return load_config(str(path) if path else str(DEFAULT_CONFIG_PATH))


def merge_dict(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


def update_processor_config(
    max_queue_size: int | None = None, queue_policy: str | None = None, path: str | Path | None = None
) -> dict[str, Any]:
    cfg = read_config(path)
    proc = cfg.setdefault("processor", {})
    if max_queue_size is not None:
        if int(max_queue_size) < 1:
            raise ValueError("max_queue_size must be >= 1")
        proc["max_queue_size"] = int(max_queue_size)
    if queue_policy is not None:
        if queue_policy not in _VALID_QUEUE_POLICIES:
            raise ValueError(f"queue_policy must be one of {_VALID_QUEUE_POLICIES}")
        proc["queue_policy"] = queue_policy
    write_config(cfg, path)
    return cfg


def update_config_partial(update: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    cfg = read_config(path)
    merge_dict(cfg, update)
    _ = AppConfig.model_validate(cfg)
    write_config(cfg, path)
    return cfg


def write_default_bus(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    default_can = CANConfig(can_db_dirs=["db/can_db/"])
    cfg = AppConfig(can=[default_can])
    default = cfg.model_dump()
    write_config(default, p)
    return default


def write_default_alarms(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_ALARMS_PATH
    try:
        from src.can_io.parser import DatabaseLoader

        loader = DatabaseLoader()
        loader.load("config/can.json")
        signals = list(loader.signals.keys())
    except Exception:
        signals = []

    alarms: dict[str, dict[str, None]] = {}
    for s in signals:
        alarms[s] = {
            "critical_high": None,
            "warning_high": None,
            "warning_low": None,
            "critical_low": None,
        }

    data = {"alarms": alarms}
    write_alarms(data, p)
    return data


@dataclass
class ConfigBackupRecord:
    backup_id: str
    file_name: str
    file_path: str
    target_path: str
    created_at: float
    creator: str
    config_version: str
    size_bytes: int


class BackupManager:
    def __init__(self, base_dir: str | Path = DEFAULT_BACKUP_DIR, retention_count: int = 20) -> None:
        self.base_dir = Path(base_dir)
        self.retention_count = max(1, int(retention_count))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.base_dir / "index.json"

    def create_backup(
        self,
        source_path: str | Path,
        *,
        creator: str,
        config_version: str = "1",
    ) -> ConfigBackupRecord:
        src = Path(source_path)
        if not src.exists():
            raise FileNotFoundError(src)
        backup_id = uuid.uuid4().hex
        ts = time.time()
        dst_name = f"{int(ts)}-{backup_id}-{src.name}"
        dst = self.base_dir / dst_name
        shutil.copy2(src, dst)
        record = ConfigBackupRecord(
            backup_id=backup_id,
            file_name=dst.name,
            file_path=str(dst),
            target_path=str(src),
            created_at=ts,
            creator=creator,
            config_version=config_version,
            size_bytes=dst.stat().st_size,
        )
        records = self._read_index()
        records.append(record)
        records = sorted(records, key=lambda item: item.created_at, reverse=True)
        self._write_index(records[: self.retention_count])
        for stale in records[self.retention_count :]:
            try:
                Path(stale.file_path).unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete stale backup %s", stale.file_path, exc_info=True)
        return record

    def list_backups(self) -> list[ConfigBackupRecord]:
        return sorted(self._read_index(), key=lambda item: item.created_at, reverse=True)

    def get_backup(self, backup_id: str) -> ConfigBackupRecord | None:
        for record in self._read_index():
            if record.backup_id == backup_id:
                return record
        return None

    def restore_backup(self, backup_id: str) -> ConfigBackupRecord:
        record = self.get_backup(backup_id)
        if record is None:
            raise FileNotFoundError(backup_id)
        shutil.copy2(record.file_path, record.target_path)
        return record

    def delete_backup(self, backup_id: str) -> bool:
        records = self._read_index()
        kept = [r for r in records if r.backup_id != backup_id]
        if len(kept) == len(records):
            return False
        record = next(r for r in records if r.backup_id == backup_id)
        Path(record.file_path).unlink(missing_ok=True)
        self._write_index(kept)
        return True

    def _read_index(self) -> list[ConfigBackupRecord]:
        if not self._index_path.exists():
            return []
        raw = json.loads(self._index_path.read_text(encoding="utf-8")) or []
        return [ConfigBackupRecord(**item) for item in raw]

    def _write_index(self, records: list[ConfigBackupRecord]) -> None:
        _write_json_atomic(self._index_path, [record.__dict__ for record in records])


class ConfigReloadManager:
    def __init__(
        self,
        *,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        alarms_path: str | Path = DEFAULT_ALARMS_PATH,
        backup_manager: BackupManager | None = None,
    ) -> None:
        self.config_path = Path(config_path)
        self.alarms_path = Path(alarms_path)
        self.backup_manager = backup_manager or BackupManager()
        self._lock = asyncio.Lock()
        self._version = 0

    async def patch_general_config(self, update: dict[str, Any], runtime=None, *, creator: str = "api") -> dict[str, Any]:
        async with self._lock:
            current = read_config(self.config_path)
            merged = copy.deepcopy(current)
            merge_dict(merged, update)
            validated = AppConfig.model_validate(merged)
            self.backup_manager.create_backup(self.config_path, creator=creator)
            write_config(validated.model_dump(), self.config_path)
            return await self._apply_runtime_reload(runtime, target="general", config=validated)

    async def reset_general_config(self, runtime=None, *, creator: str = "api") -> dict[str, Any]:
        async with self._lock:
            self.backup_manager.create_backup(self.config_path, creator=creator)
            default = write_default_bus(self.config_path)
            validated = AppConfig.model_validate(default)
            status = await self._apply_runtime_reload(runtime, target="general", config=validated)
            status["config"] = validated.model_dump()
            return status

    async def replace_alarms_config(self, payload: dict[str, Any], runtime=None, *, creator: str = "api") -> dict[str, Any]:
        async with self._lock:
            _validate_alarms_payload(payload)
            if self.alarms_path.exists():
                self.backup_manager.create_backup(self.alarms_path, creator=creator)
            write_alarms(payload, self.alarms_path)
            return await self._apply_runtime_reload(runtime, target="alarms")

    async def reset_alarms_config(self, runtime=None, *, creator: str = "api") -> dict[str, Any]:
        async with self._lock:
            if self.alarms_path.exists():
                self.backup_manager.create_backup(self.alarms_path, creator=creator)
            written = write_default_alarms(self.alarms_path)
            status = await self._apply_runtime_reload(runtime, target="alarms")
            status["written"] = written
            return status

    async def restore_backup(self, backup_id: str, runtime=None) -> dict[str, Any]:
        async with self._lock:
            record = self.backup_manager.restore_backup(backup_id)
            target = "alarms" if Path(record.target_path).name == self.alarms_path.name else "general"
            return await self._apply_runtime_reload(runtime, target=target)

    async def _apply_runtime_reload(self, runtime, *, target: str, config: AppConfig | None = None) -> dict[str, Any]:
        self._version += 1
        result = {
            "ok": True,
            "reload_version": self._version,
            "target": target,
            "applied": [],
            "skipped": [],
            "restart_required": [],
            "errors": [],
        }
        if runtime is None or not hasattr(runtime, "reload_config"):
            result["skipped"].append("runtime")
            logger.info("Config reload skipped: no runtime available for target=%s", target)
            return result
        try:
            runtime_result = await runtime.reload_config(target=target, config=config)
            for key in ("applied", "skipped", "restart_required", "errors"):
                result[key] = runtime_result.get(key, [])
            result["ok"] = not result["errors"]
            logger.info("Config reload completed target=%s ok=%s", target, result["ok"])
            return result
        except Exception as exc:
            logger.exception("Config reload failed for target=%s", target)
            result["ok"] = False
            result["errors"] = [str(exc)]
            return result


class DBCJobManager:
    def __init__(self, work_dir: str | Path = DEFAULT_DBC_WORK_DIR) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def create_job(self, dbc_name: str, dbc_content: bytes) -> dict[str, Any]:
        from scripts.dbc_utils import parse_dbc_messages

        job_id = uuid.uuid4().hex
        job_dir = self.work_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        dbc_path = job_dir / dbc_name
        dbc_path.write_bytes(dbc_content)
        parsed = parse_dbc_messages([dbc_path])
        unsupported: list[str] = []
        errors: list[str] = []
        for msg_name, msg in parsed.get("messages", {}).items():
            for sig_name, sig in msg.get("signals", {}).items():
                if sig.get("byte_order") not in {"little_endian", "big_endian", "little_endian@1", "big_endian@0", None}:
                    unsupported.append(f"{msg_name}.{sig_name}: byte_order={sig.get('byte_order')}")
                if sig.get("length") in (None, 0):
                    errors.append(f"{msg_name}.{sig_name}: invalid length")
        report = {
            "id": job_id,
            "dbc_name": dbc_name,
            "created_at": time.time(),
            "messages": len(parsed.get("messages", {})),
            "unsupported": unsupported,
            "errors": errors,
            "warnings": [],
            "parsed": parsed,
        }
        _write_json_atomic(job_dir / "report.json", report)
        return report

    def get_job(self, job_id: str) -> dict[str, Any]:
        report_path = self.work_dir / job_id / "report.json"
        if not report_path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(report_path.read_text(encoding="utf-8"))

    def generate_config(self, job_id: str, output_path: str | Path) -> dict[str, Any]:
        report = self.get_job(job_id)
        parsed = report["parsed"]
        out = Path(output_path)
        _write_json_atomic(out, parsed)
        report["generated_path"] = str(out)
        _write_json_atomic(self.work_dir / job_id / "report.json", report)
        return report


def _validate_alarms_payload(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("alarms payload must be a JSON object")
    alarms = data.get("alarms")
    if alarms is None or not isinstance(alarms, dict):
        raise ValueError("alarms payload must contain an 'alarms' object")
    for signal_name, thresholds in alarms.items():
        if not isinstance(thresholds, dict):
            raise ValueError(f"alarms.{signal_name} must be an object")
        for key, value in thresholds.items():
            if value is not None and not isinstance(value, (int, float, str)):
                raise ValueError(f"alarms.{signal_name}.{key} must be a scalar or null")


def _write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.write(fd, content)
        os.close(fd)
        fd = -1
        os.replace(tmp, str(path))
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
