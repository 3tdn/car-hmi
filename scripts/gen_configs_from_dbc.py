#!/usr/bin/env python3
"""Tạo/gộp `config/signals.json` và `config/alarms.json` từ các file DBC.

Cách dùng:
  python scripts/gen_configs_from_dbc.py --dbc path/to/file.dbc [--signals-out config/signals.json] [--alarms-out config/alarms.json] [--dry-run] [--overwrite]

Script phân tích các DBC bằng `cantools` và thêm các tín hiệu chưa có vào file JSON.
Các mục hiện có được giữ nguyên trừ khi có `--overwrite`.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, Any

import yaml

from dbc_utils import load_yaml, write_yaml, parse_dbc_files

try:
    import cantools
except Exception:  # pragma: no cover - runtime dependency
    cantools = None

logger = logging.getLogger("gen_configs_from_dbc")


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Dùng safe_dump với default_flow_style=False để YAML dễ đọc
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=True, default_flow_style=False, allow_unicode=True)


def parse_dbc_files(paths: list[Path]) -> Dict[str, Any]:
    """Trả về ánh xạ signal_name -> dict(thông tin)

    Thông tin ít nhất bao gồm: minimum, maximum, unit (khi có).
    """
    if cantools is None:
        logger.error("cantools not installed. Install with: pip install cantools")
        sys.exit(2)

    signals: dict[str, dict] = {}
    for p in paths:
        if p.is_dir():
            files = list(p.glob("**/*.dbc"))
        else:
            files = [p]
        for f in files:
            try:
                db = cantools.database.load_file(str(f))
            except Exception as exc:
                logger.warning("Failed to load %s: %s", f, exc)
                continue
            for sig in db.signals:
                # cantools Signal có thuộc tính: name, minimum, maximum, unit
                signals[sig.name] = {
                    "minimum": getattr(sig, "minimum", None),
                    "maximum": getattr(sig, "maximum", None),
                    "unit": getattr(sig, "unit", None),
                }
    return signals


def merge_into_signals_config(existing: dict, parsed: dict, overwrite: bool = False) -> dict:
    cfg = existing.copy()
    # Đảm bảo có khóa cấp cao nhất
    signals_section = cfg.get("signals") or {}
    for name, info in parsed.items():
        if name in signals_section and not overwrite:
            continue
        # Cấu hình hiển thị mặc định
        signals_section[name] = {
            "display_name": name,
            "group": "unknown",
            "widget": "gauge",
            "visible_user_mode": True,
            "writable": False,
        }
        # Nếu biết đơn vị, thêm vào làm metadata (frontend dùng unit từ DB store)
        if info.get("unit"):
            signals_section[name]["unit"] = info.get("unit")

    cfg["signals"] = signals_section
    return cfg


def merge_into_alarms_config(existing: dict, parsed: dict, overwrite: bool = False) -> dict:
    cfg = existing.copy()
    alarms_section = cfg.get("alarms") or {}
    for name, info in parsed.items():
        if name in alarms_section and not overwrite:
            continue
        # Default to all null thresholds; avoid guessing warning/critical values.
        alarms_section[name] = {
            "warning_high": None,
            "critical_high": None,
            "warning_low": None,
            "critical_low": None,
            "description": f"Auto-generated from DBC: {name}",
        }

    cfg["alarms"] = alarms_section
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate config/signals.json and config/alarms.json from DBC files")
    parser.add_argument("--dbc", "-d", required=True, nargs="+", help="DBC file(s) or directories to parse")
    parser.add_argument("--signals-out", default="config/signals.json", help="Output signals JSON path")
    parser.add_argument("--alarms-out", default="config/alarms.json", help="Output alarms JSON path")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files; just print summary")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing entries in target JSONs")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    dbc_paths = [Path(p) for p in args.dbc]
    parsed = parse_dbc_files(dbc_paths)
    logger.info("Parsed %d signals from DBC(s)", len(parsed))

    signals_path = Path(args.signals_out)
    alarms_path = Path(args.alarms_out)

    existing_signals = load_yaml(signals_path)
    existing_alarms = load_yaml(alarms_path)

    new_signals = merge_into_signals_config(existing_signals, parsed, overwrite=args.overwrite)
    new_alarms = merge_into_alarms_config(existing_alarms, parsed, overwrite=args.overwrite)

    if args.dry_run:
        # In tóm tắt các mục mới
        added_signals = set(new_signals.get("signals", {}).keys()) - set(existing_signals.get("signals", {}).keys())
        added_alarms = set(new_alarms.get("alarms", {}).keys()) - set(existing_alarms.get("alarms", {}).keys())
        logger.info("Would add %d signals and %d alarms", len(added_signals), len(added_alarms))
        if added_signals:
            logger.info("Signals to add: %s", sorted(list(added_signals))[:50])
        return 0

    write_yaml(signals_path, new_signals)
    write_yaml(alarms_path, new_alarms)
    logger.info("Wrote %s and %s", signals_path, alarms_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
