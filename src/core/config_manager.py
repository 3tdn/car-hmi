"""Quản lý cấu hình runtime (đọc/ghi config YAML cho processor).

Đây là helper tối thiểu để cập nhật `processor` trong `config/bus.yaml`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

DEFAULT_CONFIG_PATH = Path("config/bus.yaml")


def read_config(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def write_config(data: Dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def update_processor_config(
    max_queue_size: int | None = None, queue_policy: str | None = None, path: str | Path | None = None
) -> Dict[str, Any]:
    cfg = read_config(path)
    proc = cfg.setdefault("processor", {})
    if max_queue_size is not None:
        proc["max_queue_size"] = int(max_queue_size)
    if queue_policy is not None:
        proc["queue_policy"] = str(queue_policy)
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
    p = Path(path) if path else Path("config/alarms.yaml")
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def write_alarms(data: Dict[str, Any], path: str | Path | None = None) -> None:
    p = Path(path) if path else Path("config/alarms.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def write_default_bus(path: str | Path | None = None) -> Dict[str, Any]:
    """Write the default AppConfig to disk and return the default dict."""
    from src.core.config import AppConfig

    p = Path(path) if path else DEFAULT_CONFIG_PATH
    default = AppConfig().model_dump()
    # Ensure minimal CAN DB paths exist so pydantic validation (load_config) passes
    can_block = default.setdefault("can", {})
    if not can_block.get("can_db_files") and not can_block.get("can_db_dirs"):
        # set a sensible default directory that exists in repo
        can_block["can_db_dirs"] = ["db/can_db/"]
    p.write_text(yaml.safe_dump(default, sort_keys=False), encoding="utf-8")
    return default


def write_default_alarms(path: str | Path | None = None) -> Dict[str, Any]:
    """Reset alarms to an empty 'alarms' mapping (sensible default).

    Returns the written structure.
    """
    p = Path(path) if path else Path("config/alarms.yaml")
    # Try to populate default alarms for all known signals with null thresholds.
    try:
        from src.can_io.parser import DatabaseLoader

        loader = DatabaseLoader()
        # common repo locations for DB files
        loader.add_paths(["db/can_db/", "db/ecu_db/"])
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
    p.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return data
