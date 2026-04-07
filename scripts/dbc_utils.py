from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Any

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
    """Trả về ánh xạ signal_name -> dict(thông tin) sử dụng cantools.

    Thông tin bao gồm: minimum, maximum, unit (khi có).
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


def parse_dbc_messages(paths: list[Path]) -> Dict[str, Any]:
    """Trả về cấu trúc chi tiết của messages và signals từ các file DBC.

    Kết quả:
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
            # Extract CM_ SG_ comments from raw DBC text and group by signal name
            comments_by_signal: dict[str, list[str]] = defaultdict(list)
            # Also extract BU_ node list and BO_TX_BU_ transmitters mapping
            all_nodes: list[str] = []
            senders_by_id: dict[int, list[str]] = {}
            try:
                raw = f.read_text(encoding="utf-8", errors="ignore")
                for line in raw.splitlines():
                    # BU_ line lists node names
                    mbu = re.match(r'^BU_:\s*(.*)', line)
                    if mbu:
                        parts = mbu.group(1).strip()
                        if parts:
                            # split by whitespace
                            all_nodes = [p.strip() for p in parts.split() if p.strip()]
                        continue

                    # BO_TX_BU_ lines: map message id to transmitters
                    mbt = re.match(r'^BO_TX_BU_\s+(\d+)\s*:\s*(.*);', line)
                    if mbt:
                        try:
                            mid = int(mbt.group(1))
                        except Exception:
                            continue
                        rhs = mbt.group(2).strip()
                        # nodes separated by spaces and/or commas
                        nodes = [n.strip() for n in re.split(r'[ ,]+', rhs) if n.strip()]
                        senders_by_id[mid] = nodes
                        continue

                    m = re.match(r'^CM_\s+SG_\s+\d+\s+(\S+)\s+"(.*)"', line)
                    if m:
                        sig_name = m.group(1)
                        comment = m.group(2).strip()
                        # avoid duplicate comment entries when scanning raw DBC by ignoring consecutive duplicates
                        existing = comments_by_signal[sig_name]
                        if not existing or existing[-1] != comment:
                            existing.append(comment)
            except Exception:
                # ignore read errors and continue with cantools parsing
                comments_by_signal = defaultdict(list)
                all_nodes = []
                senders_by_id = {}

            try:
                db = cantools.database.load_file(str(f))
            except Exception as exc:
                logger.warning("Failed to load %s: %s", f, exc)
                continue
            for msg in getattr(db, "messages", []):
                try:
                    msg_name = msg.name or f"{msg.frame_id:#x}"
                except Exception:
                    msg_name = getattr(msg, "name", str(getattr(msg, "frame_id", "unknown")))
                signals: dict[str, dict] = {}
                for sig in getattr(msg, "signals", []):
                    # combine parsed cantools comment with any CM_ SG_ comments found in file
                    cm_comments = list(comments_by_signal.get(sig.name, []))
                    parsed_comment = getattr(sig, "comment", None)
                    # Deduplicate consecutive CM_ entries already done above; now dedupe overall preserving order
                    deduped_cm: list[str] = []
                    seen_cm: set[str] = set()
                    for c in cm_comments:
                        if not c:
                            continue
                        if c in seen_cm:
                            continue
                        seen_cm.add(c)
                        deduped_cm.append(c)

                    # Decision logic per user: if only one comment exists, prefer cantools parsed comment;
                    # if multiple comments exist, merge (CM_ comments + cantools if unique).
                    if parsed_comment and len(deduped_cm) == 0:
                        comment_val = parsed_comment
                    elif parsed_comment and len(deduped_cm) == 1:
                        # prefer cantools when a single CM_ comment exists
                        comment_val = parsed_comment
                    else:
                        # multiple CM_ comments or no parsed_comment: merge CM_ comments and include parsed if unique
                        merged: list[str] = list(deduped_cm)
                        if parsed_comment and parsed_comment not in seen_cm:
                            merged.append(parsed_comment)
                        # final dedupe to ensure uniqueness and preserve order
                        final: list[str] = []
                        seen_final: set[str] = set()
                        for c in merged:
                            if not c or c in seen_final:
                                continue
                            seen_final.add(c)
                            final.append(c)
                        comment_val = ";;".join(final) if final else None
                    signals[sig.name] = {
                        "start_bit": getattr(sig, "start", None) or getattr(sig, "start_bit", None),
                        "length": getattr(sig, "length", None),
                        "byte_order": getattr(sig, "byte_order", None),
                        "is_signed": getattr(sig, "is_signed", None),
                        "factor": getattr(sig, "scale", getattr(sig, "factor", None)),
                        "offset": getattr(sig, "offset", None),
                        "minimum": getattr(sig, "minimum", None),
                        "maximum": getattr(sig, "maximum", None),
                        "unit": getattr(sig, "unit", None),
                        "comment": comment_val,
                    }
                # determine senders: prefer BO_TX_BU_ parsed list by message id, else cantools attributes
                senders: list[str] = []
                mid = getattr(msg, "frame_id", None)
                if mid is not None and mid in senders_by_id:
                    senders = senders_by_id.get(mid, [])
                else:
                    try:
                        # cantools Message may expose 'senders' attribute
                        s = getattr(msg, "senders", None) or getattr(msg, "transmitter", None) or getattr(msg, "sender", None)
                        if s:
                            if isinstance(s, (list, tuple)):
                                senders = list(s)
                            else:
                                # comma/space separated
                                senders = [x.strip() for x in re.split(r'[ ,]+', str(s)) if x.strip()]
                    except Exception:
                        senders = []

                # receivers: nodes in BU_ that are not senders
                receivers: list[str] = []
                if all_nodes:
                    try:
                        receivers = sorted([n for n in all_nodes if n not in senders])
                    except Exception:
                        receivers = []

                result["messages"][msg_name] = {
                    "id": getattr(msg, "frame_id", None),
                    "size": getattr(msg, "length", None) or getattr(msg, "size", None),
                    "senders": senders,
                    "receivers": receivers,
                    "signals": signals,
                }
    return result
