"""Tests for CAN database parser (DBC, CANdb JSON, DatabaseLoader)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.can_io.parser import (
    CANdbJsonParser,
    DatabaseLoader,
    DBCParser,
    _extract_bits,
    _insert_bits,
)

# ── Bit helpers ───────────────────────────────────────────────────────────────


class TestBitHelpers:
    def test_extract_bits_little_endian(self):
        # 16-bit LE value at bit 0, length 16: 0x1234 stored LE = [0x34, 0x12]
        data = bytes([0x34, 0x12, 0, 0, 0, 0, 0, 0])
        val = _extract_bits(data, start_bit=0, length=16, is_signed=False, big_endian=False)
        assert val == 0x1234

    def test_extract_bits_signed(self):
        # Signed 8-bit at bit 0: value = -1 = 0xFF
        data = bytes([0xFF, 0, 0, 0, 0, 0, 0, 0])
        val = _extract_bits(data, start_bit=0, length=8, is_signed=True, big_endian=False)
        assert val == -1

    def test_extract_bits_unsigned_byte(self):
        data = bytes([0xAB, 0, 0, 0, 0, 0, 0, 0])
        val = _extract_bits(data, start_bit=0, length=8, is_signed=False, big_endian=False)
        assert val == 0xAB

    def test_insert_bits_little_endian(self):
        data = bytearray(8)
        _insert_bits(data, 0x1234, start_bit=0, length=16, is_signed=False, big_endian=False)
        assert data[0] == 0x34
        assert data[1] == 0x12

    def test_insert_and_extract_roundtrip(self):
        data = bytearray(8)
        _insert_bits(data, 42, start_bit=8, length=8, is_signed=False, big_endian=False)
        val = _extract_bits(bytes(data), start_bit=8, length=8, is_signed=False, big_endian=False)
        assert val == 42


# ── CANdb JSON Parser ────────────────────────────────────────────────────────


class TestCANdbJsonParser:
    def test_load_valid_json(self, tmp_path):
        db_file = tmp_path / "test.json"
        db_file.write_text(
            json.dumps(
                {
                    "meta": {"name": "test", "version": "1.0"},
                    "messages": {
                        "EngineStatus": {
                            "id": 608,
                            "dlc": 8,
                            "signals": {
                                "EngineRPM": {
                                    "start_bit": 0,
                                    "length": 16,
                                    "factor": 0.25,
                                    "offset": 0,
                                    "unit": "rpm",
                                    "is_signed": False,
                                    "byte_order": "little_endian",
                                }
                            },
                        }
                    },
                }
            )
        )
        parser = CANdbJsonParser()
        msgs = parser.load_file(db_file)
        assert len(msgs) == 1
        msg = msgs[0]
        assert msg.msg_id == 608
        assert msg.name == "EngineStatus"
        assert "EngineRPM" in msg.signals
        sig = msg.signals["EngineRPM"]
        assert sig.factor == 0.25
        assert sig.unit == "rpm"

    def test_load_empty_json(self, tmp_path):
        db_file = tmp_path / "empty.json"
        db_file.write_text("{}")
        parser = CANdbJsonParser()
        msgs = parser.load_file(db_file)
        assert msgs == []

    def test_supported_extensions(self):
        parser = CANdbJsonParser()
        exts = parser.supported_extensions()
        assert ".json" in exts
        assert ".candb" in exts

    def test_decode_roundtrip(self, tmp_path):
        """Encode then decode signal values for CANdb JSON parser."""
        db_file = tmp_path / "test.json"
        db_file.write_text(
            json.dumps(
                {
                    "messages": {
                        "TestMsg": {
                            "id": 100,
                            "dlc": 8,
                            "signals": {
                                "Speed": {
                                    "start_bit": 0,
                                    "length": 16,
                                    "factor": 0.01,
                                    "offset": 0,
                                    "unit": "km/h",
                                    "is_signed": False,
                                    "byte_order": "little_endian",
                                }
                            },
                        }
                    }
                }
            )
        )
        parser = CANdbJsonParser()
        msgs = parser.load_file(db_file)
        msg_dict = {m.msg_id: m for m in msgs}
        # Encode
        data = parser.encode(msg_dict, 100, {"Speed": 65.0})
        assert data is not None
        # Decode
        decoded = parser.decode(msg_dict, 100, data)
        assert decoded["Speed"] == pytest.approx(65.0, abs=0.01)


# ── DBC Parser ───────────────────────────────────────────────────────────────


class TestDBCParser:
    def test_supported_extensions(self):
        parser = DBCParser()
        exts = parser.supported_extensions()
        assert ".dbc" in exts

    def test_load_real_dbc(self):
        """Load the project's m_dummy.dbc and verify known signals."""
        dbc_path = Path("db/can_db/m_dummy.dbc")
        if not dbc_path.exists():
            pytest.skip("m_dummy.dbc not found")
        parser = DBCParser()
        msgs = parser.load_file(dbc_path)
        assert len(msgs) > 0
        # Check a known message
        msg_names = {m.name for m in msgs}
        assert "ECM_EngineStatus1" in msg_names
        # Check a known signal
        engine_msg = next(m for m in msgs if m.name == "ECM_EngineStatus1")
        assert "EngineSpeed" in engine_msg.signals
        sig = engine_msg.signals["EngineSpeed"]
        assert sig.factor == pytest.approx(0.25)
        assert sig.unit == "rpm"

    def test_load_nonexistent_dbc(self, tmp_path):
        parser = DBCParser()
        msgs = parser.load_file(tmp_path / "nope.dbc")
        assert msgs == []


# ── DatabaseLoader ────────────────────────────────────────────────────────────


class TestDatabaseLoader:
    def test_load_from_directory(self):
        """Load all DBC files from db/can_db/ and verify messages/signals."""
        db_dir = Path("db/can_db")
        if not db_dir.exists():
            pytest.skip("db/can_db not found")
        loader = DatabaseLoader()
        loader.add_paths([str(db_dir)])
        assert len(loader.messages) > 0
        assert len(loader.signals) > 0
        assert "EngineSpeed" in loader.signals

    def test_summary(self):
        loader = DatabaseLoader()
        loader.add_paths(["db/can_db"])
        s = loader.summary()
        assert "files loaded" in s
        assert "messages" in s

    def test_decode_frame(self):
        """Decode a real CAN frame using DBC."""
        db_dir = Path("db/can_db")
        if not db_dir.exists():
            pytest.skip("db/can_db not found")
        loader = DatabaseLoader()
        loader.add_paths([str(db_dir)])

        # msg_id=608 = ECM_EngineStatus1 = EngineSpeed at bit 0, 16 bits, factor=0.25
        # raw value 4000 → 4000 * 0.25 = 1000 rpm
        raw_val = 4000
        data = bytearray(8)
        data[0] = raw_val & 0xFF
        data[1] = (raw_val >> 8) & 0xFF
        decoded = loader.decode_frame(608, bytes(data))
        assert "EngineSpeed" in decoded
        assert decoded["EngineSpeed"] == pytest.approx(1000.0)

    def test_encode_signal(self):
        db_dir = Path("db/can_db")
        if not db_dir.exists():
            pytest.skip("db/can_db not found")
        loader = DatabaseLoader()
        loader.add_paths([str(db_dir)])

        msg = loader.encode_signal("EngineSpeed", 1000.0)
        assert msg is not None
        assert msg.arbitration_id == 608

    def test_encode_message(self):
        db_dir = Path("db/can_db")
        if not db_dir.exists():
            pytest.skip("db/can_db not found")
        loader = DatabaseLoader()
        loader.add_paths([str(db_dir)])

        msg = loader.encode_message(608, {"EngineSpeed": 1500.0, "CoolantTemp": 90.0})
        assert msg is not None
        assert msg.arbitration_id == 608
        assert len(msg.data) == 8

    def test_encode_unknown_signal_returns_none(self):
        loader = DatabaseLoader()
        result = loader.encode_signal("NonExistentSignal", 42.0)
        assert result is None

    def test_decode_unknown_msg_returns_empty(self):
        loader = DatabaseLoader()
        result = loader.decode_frame(0xFFFF, b"\x00\x01\x02\x03")
        assert result == {}

    def test_add_paths_missing_dir(self, tmp_path):
        """Non-existent path should log warning but not crash."""
        loader = DatabaseLoader()
        loader.add_paths([str(tmp_path / "no_such_dir")])
        assert len(loader.messages) == 0

    def test_load_json_candb(self, tmp_path):
        """Load a CANdb JSON file and verify decode/encode work."""
        db_file = tmp_path / "custom.json"
        db_file.write_text(
            json.dumps(
                {
                    "messages": {
                        "TestMsg": {
                            "id": 999,
                            "dlc": 4,
                            "signals": {
                                "Temp": {
                                    "start_bit": 0,
                                    "length": 8,
                                    "factor": 1.0,
                                    "offset": -40,
                                    "unit": "degC",
                                    "is_signed": False,
                                    "byte_order": "little_endian",
                                }
                            },
                        }
                    }
                }
            )
        )
        loader = DatabaseLoader()
        loader.add_paths([str(db_file)])
        assert 999 in loader.messages
        assert "Temp" in loader.signals

        # Decode: raw_byte=130 → 130 * 1.0 + (-40) = 90°C
        decoded = loader.decode_frame(999, bytes([130, 0, 0, 0]))
        assert decoded["Temp"] == pytest.approx(90.0)
