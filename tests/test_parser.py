"""Tests for CAN database parser (can.json based DatabaseLoader)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.can_io.parser import (
    DatabaseLoader,
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


# ── DatabaseLoader (can.json) ─────────────────────────────────────────────


class TestDatabaseLoader:
    def test_load_can_json(self):
        """Load config/can.json and verify messages/signals."""
        can_json = Path("config/can.json")
        if not can_json.exists():
            pytest.skip("config/can.json not found")
        loader = DatabaseLoader()
        loader.load(str(can_json))
        assert len(loader.messages) > 0
        assert len(loader.signals) > 0
        assert "EngineSpeed" in loader.signals

    def test_summary(self):
        loader = DatabaseLoader()
        from pathlib import Path
        can_json = Path("config/can.json")
        if not can_json.exists():
            pytest.skip("config/can.json not found")
        loader.load("config/can.json")
        s = loader.summary()
        assert "files loaded" in s
        assert "messages" in s

    def test_decode_frame(self):
        """Decode a real CAN frame using can.json."""
        loader = DatabaseLoader()
        from pathlib import Path
        can_json = Path("config/can.json")
        if not can_json.exists():
            pytest.skip("config/can.json not found")
        loader.load("config/can.json")

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
        loader = DatabaseLoader()
        from pathlib import Path
        can_json = Path("config/can.json")
        if not can_json.exists():
            pytest.skip("config/can.json not found")
        loader.load("config/can.json")

        msg = loader.encode_signal("EngineSpeed", 1000.0)
        assert msg is not None
        assert msg.arbitration_id == 608

    def test_encode_message(self):
        loader = DatabaseLoader()
        from pathlib import Path
        can_json = Path("config/can.json")
        if not can_json.exists():
            pytest.skip("config/can.json not found")
        loader.load("config/can.json")

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

    def test_load_missing_file_raises(self, tmp_path):
        """Non-existent file should raise FileNotFoundError."""
        loader = DatabaseLoader()
        with pytest.raises(FileNotFoundError):
            loader.load(str(tmp_path / "no_such_file.json"))

    def test_load_custom_json(self, tmp_path):
        """Load a custom can.json file and verify decode/encode work."""
        db_file = tmp_path / "custom.json"
        db_file.write_text(
            json.dumps(
                {
                    "messages": {
                        "TestMsg": {
                            "id": 999,
                            "size": 4,
                            "signals": {
                                "Temp": {
                                    "start_bit": 0,
                                    "length": 8,
                                    "factor": 1.0,
                                    "offset": -40,
                                    "unit": "degC",
                                    "is_signed": False,
                                    "byte_order": "little_endian",
                                    "minimum": -40,
                                    "maximum": 215,
                                }
                            },
                        }
                    }
                }
            )
        )
        loader = DatabaseLoader()
        loader.load(str(db_file))
        assert 999 in loader.messages
        assert "Temp" in loader.signals

        # Decode: raw_byte=130 → 130 * 1.0 + (-40) = 90°C
        decoded = loader.decode_frame(999, bytes([130, 0, 0, 0]))
        assert decoded["Temp"] == pytest.approx(90.0)

    def test_auto_allocate_start_bit(self, tmp_path):
        """Signals with null start_bit should be auto-allocated."""
        db_file = tmp_path / "auto.json"
        db_file.write_text(
            json.dumps(
                {
                    "messages": {
                        "TestMsg": {
                            "id": 200,
                            "size": 8,
                            "signals": {
                                "Sig1": {
                                    "start_bit": None,
                                    "length": 8,
                                    "factor": 1.0,
                                    "offset": 0,
                                    "byte_order": "little_endian",
                                },
                                "Sig2": {
                                    "start_bit": None,
                                    "length": 16,
                                    "factor": 0.1,
                                    "offset": 0,
                                    "byte_order": "little_endian",
                                },
                            },
                        }
                    }
                }
            )
        )
        loader = DatabaseLoader()
        loader.load(str(db_file))
        assert "Sig1" in loader.signals
        assert "Sig2" in loader.signals
        # Signals should have non-overlapping bit positions
        sig1 = loader.signals["Sig1"]
        sig2 = loader.signals["Sig2"]
        assert sig1.start_bit >= 0
        assert sig2.start_bit >= 0
        assert sig1.start_bit != sig2.start_bit

    def test_decode_encode_roundtrip(self, tmp_path):
        """Encode then decode should recover the same value."""
        db_file = tmp_path / "roundtrip.json"
        db_file.write_text(
            json.dumps(
                {
                    "messages": {
                        "TestMsg": {
                            "id": 100,
                            "size": 8,
                            "signals": {
                                "Speed": {
                                    "start_bit": 0,
                                    "length": 16,
                                    "factor": 0.01,
                                    "offset": 0,
                                    "unit": "km/h",
                                    "is_signed": False,
                                    "byte_order": "little_endian",
                                    "minimum": 0,
                                    "maximum": 655.35,
                                }
                            },
                        }
                    }
                }
            )
        )
        loader = DatabaseLoader()
        loader.load(str(db_file))
        msg = loader.encode_signal("Speed", 65.0)
        assert msg is not None
        decoded = loader.decode_frame(100, msg.data)
        assert decoded["Speed"] == pytest.approx(65.0, abs=0.01)
