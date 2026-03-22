#!/usr/bin/env python3
"""Tạo/gộp `config/alarms.yaml` từ các file DBC.

Cách dùng:
  python scripts/gen_alarms_from_dbc.py --dbc path/to/file.dbc [--out config/alarms.yaml] [--dry-run] [--overwrite]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dbc_utils import load_yaml, write_yaml, parse_dbc_files

logger = logging.getLogger("gen_alarms")


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
    parser = argparse.ArgumentParser(description="Generate config/alarms.yaml from DBC files")
    parser.add_argument("--dbc", "-d", required=True, nargs="+", help="DBC file(s) or directories to parse")
    parser.add_argument("--out", default="config/alarms.yaml", help="Output alarms YAML path")
    parser.add_argument("--dry-run", action="store_true", help="Don't write files; just print summary")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing entries in target YAML")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    dbc_paths = [Path(p) for p in args.dbc]
    parsed = parse_dbc_files(dbc_paths)
    logger.info("Parsed %d signals from DBC(s)", len(parsed))

    out_path = Path(args.out)
    existing = load_yaml(out_path)
    new_cfg = merge_into_alarms_config(existing, parsed, overwrite=args.overwrite)

    if args.dry_run:
        added = set(new_cfg.get("alarms", {}).keys()) - set(existing.get("alarms", {}).keys())
        logger.info("Would add %d alarms", len(added))
        if added:
            logger.info("Alarms to add: %s", sorted(list(added))[:50])
        return 0

    write_yaml(out_path, new_cfg)
    logger.info("Wrote %s", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
