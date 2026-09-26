"""In-memory signal metadata built from the active CAN database loaders."""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from src.can_io.writer import is_message_writable_by_local_node

if TYPE_CHECKING:
    from src.can_io.parser import DatabaseLoader


def _infer_tags(signal_name: str) -> list[str] | None:
    tags = [part for part in signal_name.split("_") if re.fullmatch(r"[A-Z0-9]+", part)]
    return tags or None


class SignalMetadataCatalog:
    """Process-local metadata snapshot for the DBC files loaded at startup.

    ``replace_from_loaders`` replaces the complete snapshot, so signals removed
    from a changed DBC cannot survive a reload. The runner calls it after all
    active DBC files have been parsed and before the API starts.
    """

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def replace_from_loaders(self, loaders: Iterable[DatabaseLoader]) -> None:
        items: dict[str, dict[str, Any]] = {}
        for loader in loaders:
            for message in loader.messages.values():
                writable = is_message_writable_by_local_node(message)
                for signal_name, signal in message.signals.items():
                    # Match CANWriterRouter: the first configured channel owns
                    # duplicate signal names.
                    items.setdefault(
                        signal_name,
                        {
                            "signal_name": signal_name,
                            "unit": signal.unit or None,
                            "min_value": signal.minimum,
                            "max_value": signal.maximum,
                            "group_name": None,
                            "widget_type": None,
                            "writable": writable,
                            "states": copy.deepcopy(signal.states) or None,
                            "tag": _infer_tags(signal_name),
                            "description": signal.description or None,
                            "db_source": signal.db_source,
                        },
                    )
        self._items = items

    def get(self, signal_name: str) -> dict[str, Any] | None:
        item = self._items.get(signal_name)
        return copy.deepcopy(item) if item is not None else None

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._items)

    def __len__(self) -> int:
        return len(self._items)
