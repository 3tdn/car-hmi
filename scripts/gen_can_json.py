#!/usr/bin/env python3
"""Generate `config/can.json` aggregating messages and signals from DBC files.

Usage:
  # Single directory (scans all *.dbc recursively)
  python scripts/gen_can_json.py -d db/can_db/Interface_Panther_To_CarPC_v7.dbc --out config/can0.json

  # Multiple explicit DBC files
  python scripts/gen_can_json.py -d db/candb/filedbc1.dbc db/candb/filedbc2.dbc --out config/can.json

  # Extra options
  python scripts/gen_can_json.py -d db/ --out config/can.json --dry-run
  python scripts/gen_can_json.py -d db/ --out config/can.json --overwrite
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import re
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

ROOT_DIR = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT_DIR / ".venv" / "bin" / "python"
if __name__ == "__main__" and VENV_PYTHON.exists() and Path(sys.executable).resolve() != VENV_PYTHON.resolve():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

from dbc_utils import load_json, write_json, parse_dbc_messages

logger = logging.getLogger("gen_can_json")

def infer_signal_tags(signal_name: str) -> list[str]:
    """Suy ra tag từ các phần của tên signal match `[A-Z0-9]+`"""
    return [part for part in signal_name.split("_") if re.match(r'^[A-Z0-9]+$', part)]


def _tag_signal(signal_name: str, signal_data: dict) -> dict:
    tagged = dict(signal_data)
    if not tagged.get("tag"):
        inferred = infer_signal_tags(signal_name)
        if inferred:
            tagged["tag"] = inferred
    return tagged


def _tag_messages_payload(payload: dict) -> dict:
    tagged_payload = dict(payload)
    messages = {}
    for message_name, message_data in (payload.get("messages") or {}).items():
        tagged_signals = {
            signal_name: _tag_signal(signal_name, signal_data)
            for signal_name, signal_data in (message_data.get("signals") or {}).items()
        }
        messages[message_name] = {**message_data, "signals": tagged_signals}
    tagged_payload["messages"] = messages
    return tagged_payload

def merge_messages(existing: dict, parsed: dict) -> dict:
    cfg = existing.copy()
    messages = {k: dict(v) for k, v in (cfg.get("messages") or {}).items()}
    tagged_parsed = _tag_messages_payload(parsed)
    for name, info in tagged_parsed.get("messages", {}).items():
        parsed_signals = {signal_name: dict(signal_data) for signal_name, signal_data in (info.get("signals") or {}).items()}
        if name in messages:
            merged_signals = {
                signal_name: _tag_signal(signal_name, signal_data)
                for signal_name, signal_data in (messages[name].get("signals") or {}).items()
            }
            for signal_name, signal_data in parsed_signals.items():
                if signal_name in merged_signals:
                    merged = dict(signal_data)
                    merged.update(merged_signals[signal_name])
                    merged_signals[signal_name] = _tag_signal(signal_name, merged)
                else:
                    merged_signals[signal_name] = signal_data
            messages[name] = {**info, **messages[name], "signals": merged_signals}
        else:
            messages[name] = {**info, "signals": parsed_signals}
    cfg["messages"] = messages
    return cfg

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Generate config/can.json from DBC files")
    p.add_argument("--dbc", "-d", required=True, nargs="+", help="DBC file(s) or directories to parse")
    p.add_argument("--out", default="config/can.json", help="Output path for can.json")
    p.add_argument("--dry-run", action="store_true", help="Don't write files; print summary")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing entries")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    paths = [Path(d) for d in args.dbc]
    logger.info("paths: %s", paths)
    parsed = _tag_messages_payload(parse_dbc_messages(paths))
    logger.info("Parsed %d messages", len(parsed.get("messages", {})))

    out_path = Path(args.out)
    existing: dict = {}
    if args.overwrite:
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
