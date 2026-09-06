from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any

# const CARPC_NAME = "CARPC"  # used to identify signals transmitted by this system in DBC parsing; can be customized as needed
CARPC_NAME = "CAR_PC"

try:
    import cantools
except Exception:  # pragma: no cover - runtime dependency
    cantools = None

logger = logging.getLogger("dbc_utils")


def load_yaml(path: Path) -> dict:
    """Backward-compat alias for load_json."""
    return load_json(path)


def write_yaml(path: Path, data: dict) -> None:
    """Backward-compat alias for write_json."""
    write_json(path, data)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f) or {}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_dbc_files(paths: list[Path]) -> Dict[str, Any]:
    """Return a signal_name -> dict(info) mapping using cantools.

    Information includes: minimum, maximum, unit (when available).
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
            # Cantools may expose signals via messages; iterate messages then signals
            try:
                messages = getattr(db, "messages", [])
            except Exception:
                messages = []
            for msg in messages:
                for sig in getattr(msg, "signals", []):
                    signals[sig.name] = {
                        "minimum": getattr(sig, "minimum", None),
                        "maximum": getattr(sig, "maximum", None),
                        "unit": getattr(sig, "unit", None),
                    }
    return signals

def parse_states_from_comment(states_part: str) -> list[dict[str, Any]]:
    """Parse signal states from a states_part string.

    Expects formats like:
    - "Signalvalues: 0: Off, 1: On"
    - "Signalvalues: level 1-10 x" (expands to level 1 x, level 2 x, ..., level 10 x)
    - "Signalvalues: Off; On; Error" (semicolon separated)
    """
    states: list[dict[str, Any]] = []
    if not states_part:
        return states

    parts = re.split(r'[;,]|\n', states_part)
    if len(parts) == 1:
        description = parts[0].replace("0-max ", "")
        description = description.rstrip().rstrip(".").strip()
        return [{"value": 0, "description": description}]
    idx = 0
    for part in parts:
        part = part.rstrip().rstrip(".").strip()
        # if (':' in part) or (' - ' in part) or (' = ' in part):
        if re.search(r'\s+[:\-=]\s+', part):
            val_str, desc = re.split(r'\s+[:\-=]\s+', part, maxsplit=1)
            val_str = val_str.strip()
            try:
                val = int(val_str)
            except ValueError:
                try:
                    val = float(val_str)
                except ValueError:
                    val = val_str  # keep as string if not int or float
            states.append({"value": val, "description": desc})
        elif re.search(r'\d+-\d+', part):
            m = re.match(r'(.*?)(\d+)-(\d+)(.*)', part)
            if m:
                prefix, start, end, suffix = m.groups()
                start, end = map(int, (start, end))
                if start <= end:
                    for i in range(start, end + 1):
                        states.append({"value": idx, "description": f"{prefix}{i}{suffix}"})
                        idx += 1
                else:
                    states.append({"value": idx, "description": part})
                    idx += 1
            else:
                states.append({"value": idx, "description": part})
                idx += 1
        else:
            states.append({"value": idx, "description": part})
            idx += 1
    return states

def comment_step_split(comment: str) -> tuple[str, list[dict[str, Any]]]:
    """Split a comment into main comment and states part.

    Expects formats like:
    - "Main comment | Signalvalues: 0: Off, 1: On"
    - "Main comment | Signalvalues: level 1-10 x"
    - "Main comment | Signalvalues: Off; On; Error"
    """
    if not comment or "Signalvalues:" not in comment:
        return comment, []
    
    if 'bit encoding' in comment.lower():
        # heuristic: if comment contains "bit encoding", it's likely that the whole comment is a description and not states
        return comment, []

    parts = comment.split("Signalvalues:", 1)
    main_comment = parts[0].rstrip(" ").rstrip("|").strip()
    states_part = parts[1].strip()
    states = parse_states_from_comment(states_part)
    return main_comment, states


def _as_scalar_number(value: Any, default: float = 0.0) -> float:
    """Convert NumPy/scalar/list-like values to a plain numeric scalar."""
    if value is None:
        return float(default)
    if isinstance(value, (list, tuple)):
        value = value[0] if value else default
    if hasattr(value, "item"):
        try:
            return float(value.item())
        except Exception:
            pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def parse_dbc_messages(paths: list[Path]) -> Dict[str, Any]:
    """Return a dict with the following structure:
    {
      "messages": {
         "MessageName": {
             "id": int,
             "size": int,
             "signals": { signal_name: { ... } }
         }
      }
    }
    """
    if cantools is None:
        logger.error("cantools not installed. Install with: pip install cantools")
        sys.exit(2)

    result: dict[str, dict] = {"messages": {}}
    for p in paths:
        files: list[Path]
        if p.is_dir():
            files = list(p.glob("**/*.dbc"))
        else:
            files = [p]
        for f in files:
            try:
                db = cantools.database.load_file(str(f))
            except Exception as exc:
                logger.warning("Failed to load %s: %s", f, exc)
                #thuongnt need to fix dbc by manually (TBD)
                continue
            for msg in getattr(db, "messages", []):
                try:
                    msg_name = msg.name or f"{msg.frame_id:#x}"
                except Exception:
                    msg_name = getattr(msg, "name", str(getattr(msg, "frame_id", "unknown")))

                # signals: extract info and combine cantools attributes with parsed CM_ SG_ comments for description and states
                signals: dict[str, dict] = {}
                senders = getattr(msg, "senders", [])
                for sig in getattr(msg, "signals", []):
                    comment = getattr(sig, "comment", None)
                    comments = getattr(sig, "comments", [])
                    states: list[dict[str, Any]] = []
                    comment, states = comment_step_split(comment)
                    if (comments is not None) and (len(comments) > 1):
                        comment = " | ".join(comments)
                    unit = getattr(sig, "unit", None)
                    if (unit is None) and (len(states) == 1):
                        unit = states[0].get("description").replace("0-max ", "")
                        states = []  # if we used the only state description as unit, clear states to avoid confusion
                    tx_val = CARPC_NAME in senders
                    length = getattr(sig, "length", None)
                    length = int(length) if length is not None else 0
                    is_signed = getattr(sig, "is_signed", None)
                    factor = getattr(sig, "scale", getattr(sig, "factor", 1.0))
                    offset = getattr(sig, "offset", 0)
                    factor = _as_scalar_number(factor, 1.0)
                    offset = _as_scalar_number(offset, 0.0)
                    # bound min/max to the valid bit-range implied by the signal's length, factor, and offset
                    range_minimum = (-1 * (2 ** (length - 1))) * factor + offset if is_signed else offset
                    range_maximum = (2 ** (length - 1)) * factor + offset if is_signed else (2 ** length - 1) * factor + offset
                    minimum = max(_as_scalar_number(getattr(sig, "minimum", 0), range_minimum), range_minimum)
                    maximum = min(_as_scalar_number(getattr(sig, "maximum", range_maximum), range_maximum), range_maximum)
                    name = getattr(sig, "name", "")
                    name = name.strip() if name else ""
                    # clear suffix by regex _[a-z]\w*$ to remove suffixes like _bool, _status, _flag, _state, ... (all lowcase suffixes after an underscore)
                    name = re.sub(r'_[a-z]\w*$', '', name) if name else ""
                    if name:
                        signals[name] = {
                            "start_bit": getattr(sig, "start", None) or getattr(sig, "start_bit", None),
                            "length": length,
                            "byte_order": getattr(sig, "byte_order", None),
                            "is_signed": is_signed,
                            "factor": factor,
                            "offset": offset,
                            "minimum": minimum,
                            "maximum": maximum,
                            "unit": unit,
                            "description": comment,
                            "states": states,
                            "RX": True,
                            "TX": tx_val
                        }

                result["messages"][msg_name] = {
                    "id": getattr(msg, "frame_id", None),
                    "size": getattr(msg, "length", None) or getattr(msg, "size", None),
                    "senders": senders,
                    "signals": signals,
                }
    return result
