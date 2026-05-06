"""Quản lý cấu hình runtime (đọc/ghi config JSON cho processor).

Đây là helper tối thiểu để cập nhật `processor` trong `config/system.json`.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

DEFAULT_CONFIG_PATH = Path("config/system.json")


def read_config(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    return json.loads(p.read_text(encoding="utf-8")) or {}


_VALID_QUEUE_POLICIES = {"drop_oldest", "reject"}


def write_config(data: Dict[str, Any], path: str | Path | None = None) -> None:
    """Write config atomically via temp-file + os.replace to avoid corruption on crash."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    content = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        os.write(fd, content)
        os.close(fd)
        fd = -1
        os.replace(tmp, str(p))
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

def update_processor_config(
    max_queue_size: int | None = None, queue_policy: str | None = None, path: str | Path | None = None
) -> Dict[str, Any]:
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


def validate_config(path: str | Path | None = None):
    # reuse existing load/validation in core.config when caller wants strict validation
    from src.core.config import load_config

    return load_config(str(path) if path else str(DEFAULT_CONFIG_PATH))


def merge_dict(dst: Dict[str, Any], src: Dict[str, Any]) -> Dict[str, Any]:
    """Merge src into dst recursively and return dst."""
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            merge_dict(dst[k], v)
        else:
            dst[k] = v
    return dst


def update_config_partial(update: Dict[str, Any], path: str | Path | None = None) -> Dict[str, Any]:
    cfg = read_config(path)
    merge_dict(cfg, update)
    write_config(cfg, path)
    return cfg


def read_alarms(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path else Path("config/alarms.json")
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8")) or {}


def write_alarms(data: Dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else Path("config/alarms.json")
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_default_bus(path: str | Path | None = None) -> Dict[str, Any]:
    """Write a minimal default AppConfig to disk and return the dict.

    Includes one virtual CAN channel so the written config passes validation on load.
    """
    from src.core.config import AppConfig, CANConfig

    p = Path(path) if path else DEFAULT_CONFIG_PATH
    default_can = CANConfig(can_db_dirs=["db/can_db/"])
    cfg = AppConfig(can=[default_can])
    default = cfg.model_dump()
    write_config(default, p)
    return default


def write_default_alarms(path: str | Path | None = None) -> Dict[str, Any]:
    """Reset alarms to an empty 'alarms' mapping (sensible default).

    Returns the written structure.
    """
    p = Path(path) if path else Path("config/alarms.json")
    # Try to populate default alarms for all known signals with null thresholds.
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
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data
