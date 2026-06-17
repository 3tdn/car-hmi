"""Maps CAN signal names ↔ standardized names using sync_dict config.

signal_std_name.json format: {"signal_name": "std_name", ...}

Usage:
    mapper = SignalNameMapper("config/signal_std_name.json")
    mapper.resolve("HMI_FL_OccupantAge")        # -> "HMI_FL_OccupantAge_years"  (std_name -> signal_name)
    mapper.get_std_name("HMI_FL_OccupantAge_years")  # -> "HMI_FL_OccupantAge"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SignalNameMapper:
    """Bidirectional mapper between canonical signal names and standardized names."""

    def __init__(self, sync_dict_path: str | Path | None = None) -> None:
        self._forward: dict[str, str] = {}   # signal_name -> std_name
        self._reverse: dict[str, str] = {}   # std_name -> signal_name

        if sync_dict_path:
            p = Path(sync_dict_path)
            if p.exists():
                try:
                    data: dict[str, str] = json.loads(p.read_text(encoding="utf-8")) or {}
                    self._forward = dict(data)
                    for sig_name, std in data.items():
                        if std and std != sig_name:
                            self._reverse[std] = sig_name
                    logger.info(
                        "SignalNameMapper: loaded %d entries (%d reverse aliases) from %s",
                        len(self._forward),
                        len(self._reverse),
                        p,
                    )
                except Exception:
                    logger.exception("SignalNameMapper: failed to load %s", p)
            else:
                logger.warning("SignalNameMapper: sync_dict not found at %s", p)

    def resolve(self, name: str) -> str:
        """Return canonical signal_name for *name* (which may be a std_name or signal_name).

        If *name* is a known std_name, returns the corresponding signal_name.
        Otherwise returns *name* unchanged (identity for already-canonical names).
        """
        return self._reverse.get(name, name)

    def get_std_name(self, signal_name: str) -> str | None:
        """Return std_name for *signal_name*.

        If a mapping exists in the sync dict, return the mapped `std_name`.
        Otherwise return the original `signal_name` (use signal name as fallback
        standardized name).
        """
        return self._forward.get(signal_name) or signal_name

    def __bool__(self) -> bool:
        return bool(self._forward)
