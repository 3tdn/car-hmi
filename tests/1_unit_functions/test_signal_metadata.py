"""Tests for the process-local DBC metadata catalog."""

from __future__ import annotations

from types import SimpleNamespace

from src.can_io.parser import ParsedMessage, ParsedSignal
from src.core.signal_metadata import SignalMetadataCatalog


def _loader(signal_name: str, *, unit: str = "km/h", writable: bool = True):
    signal = ParsedSignal(
        name=signal_name,
        start_bit=0,
        length=8,
        is_signed=False,
        byte_order="little_endian",
        factor=1.0,
        offset=0.0,
        unit=unit,
        minimum=0.0,
        maximum=250.0,
        description="Test signal",
        db_source="test.dbc",
        states=[{"value": 0, "description": "Off"}],
    )
    message = ParsedMessage(
        msg_id=1,
        name="TestMessage",
        dlc=8,
        senders=["CAR_PC"] if writable else ["OTHER_ECU"],
        signals={signal_name: signal},
        db_source="test.dbc",
    )
    return SimpleNamespace(messages={message.msg_id: message})


def test_catalog_builds_metadata_from_loaded_dbc_definitions():
    catalog = SignalMetadataCatalog()

    catalog.replace_from_loaders([_loader("VehicleSpeed")])

    assert catalog.get("VehicleSpeed") == {
        "signal_name": "VehicleSpeed",
        "unit": "km/h",
        "min_value": 0.0,
        "max_value": 250.0,
        "group_name": None,
        "widget_type": None,
        "writable": True,
        "states": [{"value": 0, "description": "Off"}],
        "tag": None,
        "description": "Test signal",
        "db_source": "test.dbc",
    }


def test_catalog_replacement_removes_metadata_deleted_from_dbc():
    catalog = SignalMetadataCatalog()
    catalog.replace_from_loaders([_loader("OldSignal")])

    catalog.replace_from_loaders([_loader("NewSignal", writable=False)])

    assert catalog.get("OldSignal") is None
    assert catalog.get("NewSignal")["writable"] is False


def test_catalog_returns_copies_that_callers_cannot_mutate():
    catalog = SignalMetadataCatalog()
    catalog.replace_from_loaders([_loader("VehicleSpeed")])

    external = catalog.get("VehicleSpeed")
    external["states"][0]["description"] = "Changed"

    assert catalog.get("VehicleSpeed")["states"][0]["description"] == "Off"
