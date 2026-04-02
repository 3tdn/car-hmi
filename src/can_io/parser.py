"""Bộ phân tích cơ sở dữ liệu CAN — hỗ trợ DBC (cantools), CANdb JSON, A2L (cơ bản).

Nhiệm vụ
--------
- Quét một hoặc nhiều đường dẫn file/thư mục tìm file .dbc, .a2l, .json
- Phân tích từng file và gộp tất cả định nghĩa thông điệp/tín hiệu thành một DB thống nhất
- Giải mã khung CAN thô → ``dict[signal_name, float]``
- Mã hóa giá trị tín hiệu → ``can.Message``
- Tự nhận dạng định dạng từ phần mở rộng file hoặc tham số ``can_db_format`` trong cấu hình
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import can
import cantools
import cantools.database

logger = logging.getLogger(__name__)


# ── Mô hình miền dữ liệu ───────────────────────────────────────────────────────


@dataclass
class ParsedSignal:
    """Thông tin tín hiệu đã phân tích từ file DBC / CANdb."""

    name: str
    start_bit: int
    length: int
    is_signed: bool
    byte_order: str  # "little_endian" | "big_endian"  (định dạng byte)
    factor: float
    offset: float
    unit: str
    minimum: float | None
    maximum: float | None
    description: str
    db_source: str  # file nguồn đã tải tín hiệu
    receivers: list[str] = field(default_factory=list)


@dataclass
class ParsedMessage:
    """Thông tin thông điệp đã phân tích từ file DBC / CANdb."""

    msg_id: int
    name: str
    dlc: int
    senders: list[str]
    signals: dict[str, ParsedSignal]  # signal_name → ParsedSignal (ánh xạ tín hiệu)
    db_source: str
    cycle_ms: int | None = None
    description: str = ""


# ── Giao diện ────────────────────────────────────────────────────────────────


class ICANDatabaseParser(ABC):
    """Hợp đồng cho các bộ phân tích cơ sở dữ liệu CAN."""

    @abstractmethod
    def supported_extensions(self) -> set[str]:
        """Phần mở rộng file được xử lý (v.d. {'.dbc'})."""
        ...

    @abstractmethod
    def load_file(self, path: Path) -> list[ParsedMessage]:
        """Phân tích một file và trả về danh sách định nghĩa thông điệp."""
        ...

    def decode(
        self,
        messages: dict[int, ParsedMessage],
        msg_id: int,
        data: bytes,
    ) -> dict[str, float]:
        """Giải mã mặc định theo công thức factor/offset kiểu cantools."""
        msg = messages.get(msg_id)
        if msg is None:
            return {}
        result: dict[str, float] = {}
        for sig in msg.signals.values():
            try:
                raw = _extract_bits(
                    data, sig.start_bit, sig.length, sig.is_signed, sig.byte_order == "big_endian"
                )
                result[sig.name] = raw * sig.factor + sig.offset
            except Exception as exc:
                logger.debug("Decode error signal=%s msg_id=%#x: %s", sig.name, msg_id, exc)
        return result

    def encode(
        self,
        messages: dict[int, ParsedMessage],
        msg_id: int,
        signals: dict[str, float],
    ) -> bytes | None:
        """Mã hóa dict giá trị tín hiệu thành byte dữ liệu khung CAN."""
        msg = messages.get(msg_id)
        if msg is None:
            return None
        data = bytearray(msg.dlc)
        for sig_name, value in signals.items():
            sig = msg.signals.get(sig_name)
            if sig is None:
                continue
            try:
                raw = int((value - sig.offset) / sig.factor)
                _insert_bits(
                    data,
                    raw,
                    sig.start_bit,
                    sig.length,
                    sig.is_signed,
                    sig.byte_order == "big_endian",
                )
            except Exception as exc:
                logger.debug("Encode error signal=%s: %s", sig_name, exc)
        return bytes(data)


# ── Hàm hỗ trợ thao tác bit ────────────────────────────────────────────────────


def _extract_bits(
    data: bytes, start_bit: int, length: int, is_signed: bool, big_endian: bool
) -> int:
    """Trích xuất giá trị từ byte dữ liệu khung CAN.

    Sử dụng quy ước vectorcast Intel (little-endian).
    """
    raw = int.from_bytes(data, "little")  # coi là số nguyên 64-bit little-endian
    if big_endian:
        # Motorola (MSB trước): đánh số bit khác
        # Chuyển start_bit (vị trí MSB) sang LSB
        msb_row, msb_col = divmod(start_bit, 8)
        lsb = (msb_row * 8) + msb_col - length + 1
        start = lsb
    else:
        start = start_bit
    mask = (1 << length) - 1
    value = (raw >> start) & mask
    if is_signed and (value >> (length - 1)):
        value -= 1 << length
    return value


def _insert_bits(
    data: bytearray, raw_int: int, start_bit: int, length: int, is_signed: bool, big_endian: bool
) -> None:
    """Chèn giá trị số nguyên thô vào bytearray tại vị trí bit đã chỉ định."""
    mask = (1 << length) - 1
    if is_signed and raw_int < 0:
        raw_int = raw_int & mask
    raw_int &= mask
    wide = int.from_bytes(data, "little")
    if not big_endian:
        shift = start_bit
    else:
        # Motorola: chuyển vị trí MSB sang LSB
        msb_row, msb_col = divmod(start_bit, 8)
        shift = (msb_row * 8) + msb_col - length + 1
    # Clear old bits before OR-ing new value
    wide &= ~(mask << shift)
    wide |= raw_int << shift
    data[:] = wide.to_bytes(len(data), "little")


# ── Bộ phân tích DBC \(qua cantools\) ───────────────────────────────────────────


class DBCParser(ICANDatabaseParser):
    """Phân tích file DBC sử dụng thư viện ``cantools``."""

    def supported_extensions(self) -> set[str]:
        return {".dbc", ".kcd", ".sym"}

    def load_file(self, path: Path) -> list[ParsedMessage]:
        try:
            db = cantools.database.load_file(str(path))
        except Exception as exc:
            logger.warning("cantools failed to parse %s: %s", path, exc)
            return []

        messages: list[ParsedMessage] = []
        for msg in db.messages:
            parsed_sigs: dict[str, ParsedSignal] = {}
            for sig in msg.signals:
                parsed_sigs[sig.name] = ParsedSignal(
                    name=sig.name,
                    start_bit=sig.start,
                    length=sig.length,
                    is_signed=sig.is_signed,
                    byte_order=sig.byte_order,
                    factor=float(sig.scale) if sig.scale is not None else 1.0,
                    offset=float(sig.offset) if sig.offset is not None else 0.0,
                    unit=sig.unit or "",
                    minimum=float(sig.minimum) if sig.minimum is not None else None,
                    maximum=float(sig.maximum) if sig.maximum is not None else None,
                    description=sig.comment or "",
                    db_source=path.name,
                    receivers=list(sig.receivers) if sig.receivers else [],
                )
            messages.append(
                ParsedMessage(
                    msg_id=msg.frame_id,
                    name=msg.name,
                    dlc=msg.length,
                    senders=list(msg.senders) if msg.senders else [],
                    signals=parsed_sigs,
                    db_source=path.name,
                    description=msg.comment or "",
                )
            )

        logger.info("DBC loaded: %s — %d messages", path.name, len(messages))
        return messages

    def decode(
        self,
        messages: dict[int, ParsedMessage],
        msg_id: int,
        data: bytes,
    ) -> dict[str, float]:
        """Uỷy quyền cho cantools để giải mã chính xác (xử lý đúng Motorola)."""
        msg = messages.get(msg_id)
        if msg is None:
            return {}
        # Tái sử dụng đối tượng cantools db đã tải để giải mã chính xác
        return _cantools_decode(msg.db_source, msg_id, data)

    def encode(
        self,
        messages: dict[int, ParsedMessage],
        msg_id: int,
        signals: dict[str, float],
    ) -> bytes | None:
        msg = messages.get(msg_id)
        if msg is None:
            return None
        return _cantools_encode(msg.db_source, msg_id, signals, msg.dlc)


# Bộ nhớ cache cantools DB theo module (db_source → cantools.db)
_cantools_cache: dict[str, Any] = {}


def _load_cantools(db_source: str) -> Any | None:
    return _cantools_cache.get(db_source)


def _cantools_decode(db_source: str, msg_id: int, data: bytes) -> dict[str, float]:
    db = _load_cantools(db_source)
    if db is None:
        return {}
    try:
        msg_def = db.get_message_by_frame_id(msg_id)
        decoded = msg_def.decode(data, decode_choices=False)
        return {k: float(v) for k, v in decoded.items()}
    except Exception as exc:
        logger.debug("cantools decode error msg_id=%#x: %s", msg_id, exc)
        return {}


def _cantools_encode(
    db_source: str, msg_id: int, signals: dict[str, float], dlc: int
) -> bytes | None:
    db = _load_cantools(db_source)
    if db is None:
        return bytes(dlc)
    try:
        msg_def = db.get_message_by_frame_id(msg_id)
        return msg_def.encode(signals, padding=True)
    except Exception as exc:
        logger.debug("cantools encode error msg_id=%#x: %s", msg_id, exc)
        return bytes(dlc)


# ── Bộ phân tích CANdb JSON ───────────────────────────────────────────────────


class CANdbJsonParser(ICANDatabaseParser):
    """Phân tích file CANdb JSON đơn giản (định dạng tùy chỉnh của dự án này).

    Định dạng dự kiến::

        {
            "meta": {"name": "vehicle", "version": "1.0"},
            "messages": {
                "VCU_Status": {
                    "id": 256, "dlc": 8,
                    "signals": {
                        "VehicleSpeed": {
                            "start_bit": 0, "length": 16,
                            "factor": 0.01, "offset": 0,
                            "unit": "km/h", "is_signed": false,
                            "byte_order": "little_endian"
                        }
                    }
                }
            }
        }
    """

    def supported_extensions(self) -> set[str]:
        return {".json", ".candb"}

    def load_file(self, path: Path) -> list[ParsedMessage]:
        try:
            raw: dict = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("JSON parse failed %s: %s", path, exc)
            return []

        messages: list[ParsedMessage] = []
        for msg_name, msg_data in raw.get("messages", {}).items():
            msg_id = int(msg_data["id"])
            dlc = int(msg_data.get("dlc", 8))
            parsed_sigs: dict[str, ParsedSignal] = {}
            for sig_name, sd in msg_data.get("signals", {}).items():
                parsed_sigs[sig_name] = ParsedSignal(
                    name=sig_name,
                    start_bit=int(sd["start_bit"]),
                    length=int(sd["length"]),
                    is_signed=bool(sd.get("is_signed", False)),
                    byte_order=sd.get("byte_order", "little_endian"),
                    factor=float(sd.get("factor", 1.0)),
                    offset=float(sd.get("offset", 0.0)),
                    unit=sd.get("unit", ""),
                    minimum=sd.get("min"),
                    maximum=sd.get("max"),
                    description=sd.get("description", ""),
                    db_source=path.name,
                )
            messages.append(
                ParsedMessage(
                    msg_id=msg_id,
                    name=msg_name,
                    dlc=dlc,
                    senders=[],
                    signals=parsed_sigs,
                    db_source=path.name,
                )
            )

        logger.info("CANdb JSON loaded: %s — %d messages", path.name, len(messages))
        return messages


# ── DatabaseLoader (quét nhiều file / thư mục) ─────────────────────────────

_EXT_PARSERS: dict[str, ICANDatabaseParser] = {
    ".dbc": DBCParser(),
    ".kcd": DBCParser(),
    ".sym": DBCParser(),
    ".json": CANdbJsonParser(),
    ".candb": CANdbJsonParser(),
}


class DatabaseLoader:
    """Quét đường dẫn file/thư mục, tải tất cả file DB được hỗ trợ và gộp lại.

    Sử dụng::

        loader = DatabaseLoader()
        loader.add_paths(["db/can_db/", "db/ecu_db/"])
        messages = loader.messages   # dict[msg_id → ParsedMessage]
        signals  = loader.signals    # dict[signal_name → ParsedSignal]
    """

    def __init__(self, format_hint: str = "auto") -> None:
        """
        Tham số:
            format_hint: ``"auto"`` (phát hiện theo phần mở rộng), ``"dbc"``, hoặc ``"a2l"``.
        """
        self._format_hint = format_hint
        self._messages: dict[int, ParsedMessage] = {}
        self._signals: dict[str, ParsedSignal] = {}
        self._loaded_files: list[str] = []
        # Hỗ trợ A2L: được trì hoãn (yêu cầu pya2l — phụ thuộc tùy chọn)
        self._a2l_parser: ICANDatabaseParser | None = _try_load_a2l_parser()

    # ── API công khai ──────────────────────────────────────────────────────────

    def add_paths(self, paths: list[str]) -> None:
        """Quét từng đường dẫn (file hoặc thư mục) và tải tất cả file DB được hỗ trợ."""
        for p in paths:
            resolved = Path(p)
            if not resolved.exists():
                logger.warning("DB path not found: %s", resolved)
                continue
            if resolved.is_dir():
                self._scan_dir(resolved)
            else:
                self._load_one(resolved)

    @property
    def messages(self) -> dict[int, ParsedMessage]:
        return self._messages

    @property
    def signals(self) -> dict[str, ParsedSignal]:
        return self._signals

    def decode_frame(self, msg_id: int, data: bytes) -> dict[str, float]:
        """Giải mã byte CAN thô → dict tín hiệu sử dụng cô sở dữ liệu đã tải."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return {}
        parser = self._get_parser(Path(msg.db_source))
        return parser.decode(self._messages, msg_id, data)

    def encode_signal(self, signal_name: str, value: float) -> can.Message | None:
        """Tìm thông điệp chứa ``signal_name`` và mã hóa nó."""
        sig = self._signals.get(signal_name)
        if sig is None:
            logger.debug("Signal not found in DB: %s", signal_name)
            return None
        # Tìm thông điệp sở hữu tín hiệu này
        for msg in self._messages.values():
            if signal_name in msg.signals:
                parser = self._get_parser(Path(msg.db_source))
                data = parser.encode(self._messages, msg.msg_id, {signal_name: value})
                if data is None:
                    return None
                return can.Message(
                    arbitration_id=msg.msg_id,
                    data=data,
                    is_extended_id=msg.msg_id > 0x7FF,
                )
        return None

    def encode_message(self, msg_id: int, signals: dict[str, float]) -> can.Message | None:
        """Mã hóa toàn bộ thông điệp theo ID với nhiều giá trị tín hiệu."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return None
        parser = self._get_parser(Path(msg.db_source))
        data = parser.encode(self._messages, msg_id, signals)
        if data is None:
            return None
        return can.Message(
            arbitration_id=msg_id,
            data=data,
            is_extended_id=msg_id > 0x7FF,
        )

    # ── Phương thức riêng ───────────────────────────────────────────────────────

    def _scan_dir(self, directory: Path) -> None:
        supported = set(_EXT_PARSERS.keys())
        if self._a2l_parser:
            supported.add(".a2l")
        for f in sorted(directory.iterdir()):
            if f.is_file() and f.suffix.lower() in supported:
                self._load_one(f)

    def _load_one(self, path: Path) -> None:
        parser = self._get_parser(path)
        msgs = parser.load_file(path)
        for msg in msgs:
            if msg.msg_id in self._messages:
                # Gộp tín hiệu từ các định nghĩa thông điệp trùng lặp
                self._messages[msg.msg_id].signals.update(msg.signals)
            else:
                self._messages[msg.msg_id] = msg
            for sig_name, sig in msg.signals.items():
                self._signals[sig_name] = sig
            # Lưu cache đối tượng cantools DB nếu là DBC
            if path.suffix.lower() in {".dbc", ".kcd", ".sym"}:
                try:
                    ct_db = cantools.database.load_file(str(path))
                    _cantools_cache[path.name] = ct_db
                except Exception:
                    pass
        self._loaded_files.append(str(path))
        logger.debug("Loaded %s (%d msgs, %d sigs total)", path.name, len(msgs), len(self._signals))

    def _get_parser(self, path: Path) -> ICANDatabaseParser:
        ext = path.suffix.lower()
        if ext == ".a2l" and self._a2l_parser:
            return self._a2l_parser
        return _EXT_PARSERS.get(ext, CANdbJsonParser())

    def summary(self) -> str:
        return (
            f"DatabaseLoader: {len(self._loaded_files)} files loaded, "
            f"{len(self._messages)} messages, {len(self._signals)} signals"
        )


def _try_load_a2l_parser() -> ICANDatabaseParser | None:
    """Trả về bộ phân tích A2L nếu pya2l được cài, ngược lại trả về None."""
    try:
        import pya2l  # noqa: F401

        return _A2LParser()
    except ImportError:
        logger.debug("pya2l not installed — A2L files will be skipped")
        return None


class _A2LParser(ICANDatabaseParser):
    """Cầu nối cơ bản A2L MEASUREMENT → tín hiệu (yêu cầu pya2l)."""

    def supported_extensions(self) -> set[str]:
        return {".a2l"}

    def load_file(self, path: Path) -> list[ParsedMessage]:
        """A2L không ánh xạ trực tiếp vào các thông điệp CAN — trả về danh sách rỗng.

        Các Measurement được tải ở đây được đăng ký́ trong thông điệp ảo (id=0xFFFFFFFE)
        chỉ dùng cho đường tích hợp signal-injection của simulator.
        """
        try:
            import pya2l

            a2l = pya2l.load(str(path))
        except Exception as exc:
            logger.warning("A2L parse failed %s: %s", path, exc)
            return []

        sigs: dict[str, ParsedSignal] = {}
        for meas in getattr(a2l.module, "measurement", []):
            sigs[meas.name] = ParsedSignal(
                name=meas.name,
                start_bit=0,
                length=16,
                is_signed=False,
                byte_order="little_endian",
                factor=1.0,
                offset=0.0,
                unit=getattr(meas, "phys_unit", ""),
                minimum=None,
                maximum=None,
                description=getattr(meas, "longIdentifier", ""),
                db_source=path.name,
            )

        if not sigs:
            return []

        virtual_msg = ParsedMessage(
            msg_id=0xFFFFF_FE,  # special virtual ID — not a real CAN frame
            name=f"_A2L_{path.stem}",
            dlc=8,
            senders=[],
            signals=sigs,
            db_source=path.name,
        )
        logger.info("A2L loaded: %s — %d measurements", path.name, len(sigs))
        return [virtual_msg]

    def decode(self, messages, msg_id, data):
        return {}  # Thông điệp ảo A2L không được giải mã từ bus

    def encode(self, messages, msg_id, signals):
        return None  # Giá trị A2L được nạp trực tiếp, không qua khung CAN
