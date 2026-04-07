#!/usr/bin/env python3
"""Generate `config/signals.json` from `config/can.json`.

Creates an array of signal objects with metadata and parsed state enums
derived from comments (e.g. detects "Signalvalues: ...").

Usage:
  python scripts/gen_signals_from_canjson.py --in config/can.json --out config/signals.json [--dry-run] [-v]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("gen_signals_from_canjson")


def parse_states_from_comment(comment: str) -> tuple[list[dict[str, Any]], str | None]:
    if not comment:
        return [], None
    # look for token 'Signalvalues:' (case-insensitive)
    m = re.search(r"Signalvalues:\s*(.+)$", comment, flags=re.IGNORECASE)
    if not m:
        return [], None
    vals = m.group(1)
    vals = vals.strip()
    # If there's no comma/semicolon and no numeric range, treat the whole value as a unit candidate
    if "," not in vals and ";" not in vals and not re.search(r"\d+\s*-\s*\d+", vals):
        return [], vals
    # split by commas or ';'
    raw_parts = [p.strip() for p in re.split(r"[,;]", vals) if p.strip()]
    expanded: list[str] = []
    for p in raw_parts:
        # match patterns like 'Level 1-7' or 'Haptic 1-6' or 'Retract 1-4'
        mrange = re.match(r"^(.*?)(\d+)\s*-\s*(\d+)$", p)
        if mrange:
            prefix = mrange.group(1).strip()
            start = int(mrange.group(2))
            end = int(mrange.group(3))
            if prefix:
                for n in range(start, end + 1):
                    expanded.append(f"{prefix} {n}")
            else:
                for n in range(start, end + 1):
                    expanded.append(str(n))
            continue
        # match patterns like 'Haptic 1-6' with a space before range
        mrange2 = re.match(r"^(.*?\D)\s*(\d+)\s*-\s*(\d+)$", p)
        if mrange2:
            prefix = mrange2.group(1).strip()
            start = int(mrange2.group(2))
            end = int(mrange2.group(3))
            for n in range(start, end + 1):
                expanded.append(f"{prefix} {n}")
            continue
        expanded.append(p)

    states: list[dict[str, Any]] = []
    for i, desc in enumerate(expanded):
        states.append({"value": i, "description": desc})
    return states, None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Generate signals.json from can.json")
    p.add_argument("--in", dest="input", default="config/can.json", help="Input can.json")
    p.add_argument("--out", dest="out", default="config/signals.json", help="Output signals.json")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    in_path = Path(args.input)
    if not in_path.exists():
        logger.error("Input file %s not found", in_path)
        return 2

    data = json.loads(in_path.read_text(encoding="utf-8"))
    messages = data.get("messages", {})

    signals_out: list[dict[str, Any]] = []
    for msg_name, msg in messages.items():
        senders = list(msg.get("senders") or [])
        # destinations: try to infer from message-level transmitters when present in can.json
        # If senders list present, other known nodes cannot be inferred here; leave destination empty if not available.
        destinations = []
        # if senders present and there is known BU list in top-level, not available here. Keep destinations empty.

        for sig_name, sig in msg.get("signals", {}).items():
            comment = sig.get("comment")
            # description: prefer cantools parsed comment if appears before 'Signalvalues', else take entire comment
            description = None
            if comment:
                # prefer first part up to ';;' or 'Signalvalues:'
                description = re.split(r";;|Signalvalues:", comment, maxsplit=1)[0].strip()
            unit = sig.get("unit") or ""
            minimum = sig.get("minimum")
            maximum = sig.get("maximum")

            states, unit_candidate = parse_states_from_comment(comment or "")

            # destination: use message receivers if present
            destinations = msg.get("receivers") or []

            # TX: true if CAR_PC is present in senders
            tx = ("CAR_PC" in senders)
            # RX: true if there is at least one destination (receiver)
            rx = bool(destinations)

            # If no explicit unit but Signalvalues provides a single token (unit), use it
            unit = sig.get("unit") or ""
            if (not unit or unit == "") and unit_candidate:
                unit = unit_candidate

            signals_out.append(
                {
                    "name": sig_name,
                    "value": 0,
                    "source": senders,
                    "destination": destinations,
                    "timestamp": 0,
                    "description": description or "",
                    "unit": unit,
                    "min": minimum if minimum is not None else 0,
                    "max": maximum if maximum is not None else 0,
                    "RX": rx,
                    "TX": tx,
                    "states": states,
                }
            )

    out_path = Path(args.out)
    if args.dry_run:
        logger.info("Would write %d signals to %s", len(signals_out), out_path)
        return 0

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"signals": signals_out}, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Wrote %s with %d signals", out_path, len(signals_out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
