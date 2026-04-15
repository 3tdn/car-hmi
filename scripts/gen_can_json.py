#!/usr/bin/env python3
"""Generate `config/can.json` aggregating messages and signals from DBC files.

Usage:
  python scripts/gen_can_json.py -d db/ --out config/can.json [--dry-run] [--overwrite]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from dbc_utils import load_json, write_json, parse_dbc_messages

logger = logging.getLogger("gen_can_json")

def merge_messages(existing: dict, parsed: dict) -> dict:
    cfg = existing.copy()
    messages = cfg.get("messages") or {}
    for name, info in parsed.get("messages", {}).items():
        messages[name] = info
    cfg["messages"] = messages
    return cfg

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate config/can.json from DBC files")
    p.add_argument("--dbc", "-d", required=True, nargs="+", help="DBC file(s) or directories to parse")
    p.add_argument("--out", default="config/can.json", help="Output path for can.json")
    p.add_argument("--dry-run", action="store_true", help="Don't write files; print summary")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing entries")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    paths = [Path(p) for p in args.dbc]
    logger.info("paths: %s", paths)
    parsed = parse_dbc_messages(paths)
    logger.info("Parsed %d messages", len(parsed.get("messages", {})))

    out_path = Path(args.out)
    if (args.overwrite):
        new_cfg = parsed
    else:
        try:
            existing = load_json(out_path)
            new_cfg = merge_messages(existing, parsed)
        except FileNotFoundError:
            new_cfg = parsed

    if args.dry_run:
        added = set(new_cfg.get("messages", {}).keys()) - set(existing.get("messages", {}).keys())
        logger.info("Would add %d messages", len(added))
        if added:
            logger.info("Messages to add: %s", sorted(list(added))[:50])
        return 0

    write_json(out_path, new_cfg)
    logger.info("Wrote %s", out_path)
    return 0

if False:
    main(["-d", ".\\db\\can_db\\p_v2.dbc", "--out", "config/can2.json", "--overwrite"])
    exit(0)

if __name__ == "__main__":
    raise SystemExit(main())
