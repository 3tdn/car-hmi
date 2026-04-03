from __future__ import annotations

import json
import logging
import sys
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
