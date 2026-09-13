"""Safe system-config updates, fixed-path backup/reset, and runtime reload."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import ValidationError

from src.core.config import AppConfig
from src.core.config_policy import (
    ReloadLevel,
    classify_paths,
    diff_paths,
    match_policy,
    policy_payload,
    validate_policy_values,
)
from src.core.paths import (
    DEFAULT_CONFIG_BACKUP_DIR,
    DEFAULT_CONFIG_PATH,
    DEFAULT_CONFIG_TEMPLATE_PATH,
)


class ConfigUpdateError(ValueError):
    """A stable API-facing system configuration error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_config(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    return json.loads(target.read_text(encoding="utf-8")) or {}


def write_config(data: dict[str, Any], path: str | Path | None = None) -> None:
    """Write JSON atomically via a sibling temporary file and ``os.replace``."""
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    fd, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def merge_dict(destination: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge mappings; lists and scalar values are replaced atomically."""
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(destination.get(key), dict):
            merge_dict(destination[key], value)
        else:
            destination[key] = deepcopy(value)
    return destination


def _validate(raw: dict[str, Any]) -> AppConfig:
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigUpdateError("system_config_validation_failed", str(exc)) from exc


def _validate_field_values(raw: dict[str, Any], changed: list[str]) -> None:
    errors = validate_policy_values(raw, changed)
    if errors:
        raise ConfigUpdateError("system_config_field_validation_failed", "; ".join(errors))


class SystemConfigManager:
    """Manage exactly one config file and its project-local backup directory."""

    _BACKUP_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
    _RESERVED_BACKUP_IDS: ClassVar[frozenset[str]] = frozenset({"index"})

    def __init__(
        self,
        config_path: str | Path = DEFAULT_CONFIG_PATH,
        template_path: str | Path = DEFAULT_CONFIG_TEMPLATE_PATH,
        backup_dir: str | Path = DEFAULT_CONFIG_BACKUP_DIR,
    ) -> None:
        self.config_path = Path(config_path).resolve()
        self.template_path = Path(template_path).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self._lock = asyncio.Lock()

    def read(self) -> dict[str, Any]:
        raw = read_config(self.config_path)
        _validate(raw)
        return raw

    def public_snapshot(self) -> dict[str, Any]:
        raw = self._redact(self.read())
        return {
            "config": raw,
            **policy_payload(),
            "paths": {
                "config": "config/system.json",
                "reset_template": "config/system_bk.json",
                "field_definitions": "config/system.fields.json",
                "backup_directory": "config/backups",
            },
        }

    @staticmethod
    def _redact(raw: dict[str, Any]) -> dict[str, Any]:
        raw = deepcopy(raw)
        if isinstance(raw.get("api"), dict) and "api_key" in raw["api"]:
            raw["api"]["api_key"] = "********"
        return raw

    def _retention_count(self, raw: dict[str, Any] | None = None) -> int:
        raw = raw if raw is not None else self.read()
        value = raw.get("config_management", {}).get("backup_retention_count", 20)
        return min(200, max(1, int(value)))

    def _resolve_backup(self, backup_id: str) -> Path:
        if not self._BACKUP_ID.fullmatch(backup_id):
            raise ConfigUpdateError("system_config_backup_invalid", "Invalid backup id")
        if backup_id in self._RESERVED_BACKUP_IDS:
            raise ConfigUpdateError(
                "system_config_backup_not_found", f"Backup '{backup_id}' not found"
            )
        path = (self.backup_dir / f"{backup_id}.json").resolve()
        if path.parent != self.backup_dir:
            raise ConfigUpdateError("system_config_backup_invalid", "Invalid backup path")
        if not path.is_file():
            raise ConfigUpdateError(
                "system_config_backup_not_found", f"Backup '{backup_id}' not found"
            )
        return path

    def create_backup(
        self, reason: str = "manual", raw: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        source = raw if raw is not None else self.read()
        _validate(source)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe_reason = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-") or "manual"
        timestamp = datetime.now(UTC)
        backup_id = f"{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}_{uuid4().hex[:8]}_{safe_reason}"
        target = self.backup_dir / f"{backup_id}.json"
        write_config(source, target)
        self._prune_backups(self._retention_count(source))
        return self._backup_record(target)

    @staticmethod
    def _backup_record(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "id": path.stem,
            "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "size_bytes": stat.st_size,
        }

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_dir.exists():
            return []
        paths = sorted(
            (
                path
                for path in self.backup_dir.glob("*.json")
                if path.stem not in self._RESERVED_BACKUP_IDS
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return [self._backup_record(path) for path in paths]

    def _prune_backups(self, keep: int) -> None:
        for record in self.list_backups()[keep:]:
            self._resolve_backup(record["id"]).unlink()

    async def patch(self, update: dict[str, Any], runner: Any = None) -> dict[str, Any]:
        if not isinstance(update, dict) or not update:
            raise ConfigUpdateError(
                "system_config_patch_empty", "Patch body must be a non-empty object"
            )
        async with self._lock:
            current = self.read()
            merged = merge_dict(deepcopy(current), update)
            validated = _validate(merged)
            changed = diff_paths(current, merged)
            unsupported = [path for path in changed if match_policy(path) is None]
            immutable = [
                path
                for path in changed
                if (policy := match_policy(path)) is not None
                and policy.reload_level == ReloadLevel.IMMUTABLE
            ]
            if unsupported:
                raise ConfigUpdateError(
                    "system_config_field_unsupported",
                    f"Unsupported config field(s): {', '.join(unsupported)}",
                )
            if immutable:
                raise ConfigUpdateError(
                    "system_config_field_immutable",
                    f"Immutable config field(s): {', '.join(immutable)}",
                )
            _validate_field_values(merged, changed)
            if not changed:
                return await self._result(merged, changed, runner, validated, apply=False)
            backup = self.create_backup("auto-before-update", current)
            return await self._commit_with_runtime_rollback(
                current=current,
                updated=merged,
                changed=changed,
                runner=runner,
                validated=validated,
                backup=backup,
            )

    async def reset(self, runner: Any = None) -> dict[str, Any]:
        async with self._lock:
            current = self.read()
            template = read_config(self.template_path)
            validated = _validate(template)
            changed = diff_paths(current, template)
            _validate_field_values(template, changed)
            backup = self.create_backup("auto-before-reset", current)
            return await self._commit_with_runtime_rollback(
                current=current,
                updated=template,
                changed=changed,
                runner=runner,
                validated=validated,
                backup=backup,
            )

    async def restore(self, backup_id: str, runner: Any = None) -> dict[str, Any]:
        async with self._lock:
            current = self.read()
            restored = read_config(self._resolve_backup(backup_id))
            validated = _validate(restored)
            changed = diff_paths(current, restored)
            _validate_field_values(restored, changed)
            safety_backup = self.create_backup("auto-before-restore", current)
            return await self._commit_with_runtime_rollback(
                current=current,
                updated=restored,
                changed=changed,
                runner=runner,
                validated=validated,
                backup=safety_backup,
            )

    async def _commit_with_runtime_rollback(
        self,
        *,
        current: dict[str, Any],
        updated: dict[str, Any],
        changed: list[str],
        runner: Any,
        validated: AppConfig,
        backup: dict[str, Any],
    ) -> dict[str, Any]:
        """Persist config and restore disk/runtime state if live apply raises."""
        write_config(updated, self.config_path)
        try:
            return await self._result(updated, changed, runner, validated, backup=backup)
        except Exception as apply_exc:
            rollback_errors: list[str] = []
            try:
                write_config(current, self.config_path)
            except Exception as exc:
                rollback_errors.append(f"disk rollback failed: {exc}")

            if runner is not None and changed:
                try:
                    previous_config = _validate(current)
                    await runner.apply_system_config(previous_config, changed)
                except Exception as exc:
                    rollback_errors.append(f"runtime rollback failed: {exc}")

            detail = f"Runtime config apply failed: {apply_exc}"
            if rollback_errors:
                detail = f"{detail}; {'; '.join(rollback_errors)}"
            raise ConfigUpdateError("system_config_runtime_apply_failed", detail) from apply_exc

    async def reload_runtime(self, runner: Any) -> dict[str, Any]:
        raw = self.read()
        validated = _validate(raw)
        if runner is None:
            raise ConfigUpdateError(
                "system_config_runtime_unavailable", "Application runner is unavailable"
            )
        old_raw = runner.config.model_dump(mode="json")
        new_runtime = validated.model_dump(mode="json")
        changed = diff_paths(old_raw, new_runtime)
        _validate_field_values(raw, changed)
        return await self._result(raw, changed, runner, validated)

    async def _result(
        self,
        raw: dict[str, Any],
        changed: list[str],
        runner: Any,
        validated: AppConfig,
        *,
        backup: dict[str, Any] | None = None,
        apply: bool = True,
    ) -> dict[str, Any]:
        levels = classify_paths(changed)
        runtime: dict[str, list[str]] = {"applied": [], "unavailable": []}
        if apply and changed:
            if runner is None and levels[ReloadLevel.LIVE.value]:
                runtime["unavailable"] = list(levels[ReloadLevel.LIVE.value])
            elif runner is not None:
                runtime.update(await runner.apply_system_config(validated, changed) or {})
        runner_pending = getattr(runner, "pending_reboot_paths", None)
        if runner_pending is None:
            pending_reboot = sorted(
                set(levels[ReloadLevel.REBOOT.value]) | set(levels[ReloadLevel.IMMUTABLE.value])
            )
        else:
            pending_reboot = sorted(runner_pending)
        response: dict[str, Any] = {
            "ok": True,
            "config": self._redact(raw),
            "changed_paths": changed,
            "reload": levels,
            "runtime": runtime,
            "pending_reboot_paths": pending_reboot,
            "reboot_required": bool(pending_reboot),
        }
        if backup is not None:
            response["backup"] = backup
        return response


# Compatibility helper used by scripts/set_processor_config.py.
def update_processor_config(
    max_queue_size: int | None = None,
    queue_policy: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    update: dict[str, Any] = {"processor": {}}
    if max_queue_size is not None:
        update["processor"]["max_queue_size"] = int(max_queue_size)
    if queue_policy is not None:
        update["processor"]["queue_policy"] = queue_policy
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    current = read_config(target)
    merged = merge_dict(current, update)
    _validate(merged)
    write_config(merged, target)
    return merged
