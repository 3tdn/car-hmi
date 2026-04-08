"""Bộ phân tích cơ sở dữ liệu CAN — tải từ file can.json.

Nhiệm vụ
--------
- Tải file can.json chứa định nghĩa thông điệp/tín hiệu
- Tự động phân bổ ``start_bit`` khi giá trị là ``null``
- Tự động tính ``minimum``/``maximum`` khi thiếu
- Giải mã khung CAN thô → ``dict[signal_name, float]``
- Mã hóa giá trị tín hiệu → ``can.Message``
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import can

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


# ── Hàm hỗ trợ thao tác bit ────────────────────────────────────────────────────


def _mark_used_bits(used: list[bool], start_lsb: int, length: int) -> None:
    """Đánh dấu các bit đã dùng trong bitmask ``used`` (LSB-indexed)."""
    total = len(used)
    for b in range(max(0, start_lsb), min(total, start_lsb + length)):
        used[b] = True


def _extract_bits(
    data: bytes, start_bit: int, length: int, is_signed: bool, big_endian: bool
) -> int:
    """Trích xuất giá trị từ byte dữ liệu khung CAN.

    Sử dụng quy ước vectorcast Intel (little-endian).
    """
    raw = int.from_bytes(data, "little")
    if big_endian:
        msb_row, msb_col = divmod(start_bit, 8)
        lsb = (msb_row * 8) + msb_col - length + 1
        if lsb < 0:
            raise ValueError(
                f"Big-endian start_bit={start_bit} length={length} "
                f"yields negative LSB={lsb}"
            )
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
        msb_row, msb_col = divmod(start_bit, 8)
        shift = (msb_row * 8) + msb_col - length + 1
        if shift < 0:
            raise ValueError(
                f"Big-endian start_bit={start_bit} length={length} "
                f"yields negative shift={shift}"
            )
    # Clear old bits before OR-ing new value
    wide &= ~(mask << shift)
    wide |= raw_int << shift
    data[:] = wide.to_bytes(len(data), "little")


# ── Giải mã / Mã hóa frame ──────────────────────────────────────────────────


def decode_frame_from_msg(msg: ParsedMessage, data: bytes) -> dict[str, float]:
    """Giải mã byte CAN thô → dict tín hiệu theo công thức factor/offset."""
    if len(data) < msg.dlc:
        # Pad short frames to avoid bit extraction errors
        data = data + b"\x00" * (msg.dlc - len(data))
    result: dict[str, float] = {}
    for sig in msg.signals.values():
        try:
            raw = _extract_bits(
                data, sig.start_bit, sig.length, sig.is_signed, sig.byte_order == "big_endian"
            )
            result[sig.name] = raw * sig.factor + sig.offset
        except Exception as exc:
            logger.debug("Decode error signal=%s msg_id=%#x: %s", sig.name, msg.msg_id, exc)
    return result


def encode_frame_from_msg(msg: ParsedMessage, signals: dict[str, float]) -> bytes:
    """Mã hóa dict giá trị tín hiệu thành byte dữ liệu khung CAN."""
    data = bytearray(msg.dlc)
    for sig_name, value in signals.items():
        sig = msg.signals.get(sig_name)
        if sig is None:
            logger.debug(
                "Encode: signal '%s' not in message '%s' (id=%#x) — skipped",
                sig_name, msg.name, msg.msg_id,
            )
            continue
        try:
            raw = int((value - sig.offset) / sig.factor) if sig.factor != 0 else 0
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


# ── DatabaseLoader — tải từ can.json ────────────────────────────────────────


class DatabaseLoader:
    """Tải cơ sở dữ liệu CAN từ file ``can.json``.

    Hỗ trợ:
    - Tự động phân bổ ``start_bit`` khi giá trị là ``null``
    - Tự động tính ``minimum``/``maximum`` khi thiếu
    - Giải mã / mã hóa khung CAN qua công thức factor/offset

    Sử dụng::

        loader = DatabaseLoader()
        loader.load("config/can.json")
        messages = loader.messages   # dict[msg_id → ParsedMessage]
        signals  = loader.signals    # dict[signal_name → ParsedSignal]
    """

    def __init__(self) -> None:
        self._messages: dict[int, ParsedMessage] = {}
        self._signals: dict[str, ParsedSignal] = {}
        self._signal_to_msg: dict[str, int] = {}  # signal_name → msg_id
        self._loaded_files: list[str] = []

    # ── API công khai ──────────────────────────────────────────────────────────

    def load(self, path: str | Path) -> None:
        """Tải file can.json và gộp định nghĩa thông điệp/tín hiệu."""
        resolved = Path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"can.json not found: {resolved}")
        try:
            raw: dict = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"JSON parse failed {resolved}: {exc}") from exc

        skipped_no_bit = 0
        auto_filled_range = 0
        loaded_msg_count = 0

        for msg_name, md in raw.get("messages", {}).items():
            raw_id = md.get("id")
            if raw_id is None:
                logger.warning("Skip message '%s' — missing 'id' field", msg_name)
                continue
            msg_id = int(raw_id)
            dlc = int(md.get("size", md.get("dlc", 8)))
            senders = md.get("senders", [])
            description = md.get("comment", md.get("description", ""))

            # Thu thập raw signal trước để phân bổ start_bit
            raw_sigs: list[dict] = []
            for sig_name, sd in md.get("signals", {}).items():
                raw_len = sd.get("length")
                if raw_len is None:
                    logger.warning(
                        "Skip signal '%s' in '%s' — missing 'length'", sig_name, msg_name,
                    )
                    continue
                length = int(raw_len)
                if length <= 0:
                    logger.warning(
                        "Skip signal '%s' in '%s' — invalid length=%d",
                        sig_name, msg_name, length,
                    )
                    continue
                factor = float(sd.get("factor", 1.0))
                if factor == 0.0:
                    logger.warning(
                        "Signal '%s' in '%s' has factor=0, defaulting to 1.0",
                        sig_name, msg_name,
                    )
                    factor = 1.0
                raw_sigs.append({
                    "name": sig_name,
                    "start_bit": sd.get("start_bit"),
                    "length": length,
                    "is_signed": bool(sd.get("is_signed", False)),
                    "big_endian": sd.get("byte_order", "little_endian") == "big_endian",
                    "factor": factor,
                    "offset": float(sd.get("offset", 0.0)),
                    "minimum": sd.get("minimum", sd.get("min")),
                    "maximum": sd.get("maximum", sd.get("max")),
                    "unit": sd.get("unit", "") or "",
                    "comment": sd.get("comment", sd.get("description", "")) or "",
                    "receivers": sd.get("receivers", []),
                })

            # Tìm bit đã dùng (LSB-indexed) — lần quét 1
            total_bits = dlc * 8
            used: list[bool] = [False] * total_bits

            for rs in raw_sigs:
                sb = rs["start_bit"]
                if sb is None:
                    continue
                sb = int(sb)
                if not rs["big_endian"]:
                    start_lsb = sb
                else:
                    msb_row, msb_col = divmod(sb, 8)
                    start_lsb = (msb_row * 8) + msb_col - rs["length"] + 1
                if start_lsb >= 0 and start_lsb + rs["length"] <= total_bits:
                    _mark_used_bits(used, start_lsb, rs["length"])

            # Lần quét 2: xây ParsedSignal, phân bổ start_bit nếu null
            parsed_sigs: dict[str, ParsedSignal] = {}
            for rs in raw_sigs:
                name = rs["name"]
                length = rs["length"]
                is_signed = rs["is_signed"]
                big_endian = rs["big_endian"]
                factor = rs["factor"]
                offset = rs["offset"]

                sb = rs["start_bit"]
                if sb is None:
                    # Phân bổ tự động: tìm khoảng bit trống đầu tiên
                    found = False
                    for p in range(0, total_bits - length + 1):
                        if all(not used[b] for b in range(p, p + length)):
                            assigned_sb = p + length - 1 if big_endian else p
                            sb = assigned_sb
                            _mark_used_bits(used, p, length)
                            found = True
                            break
                    if not found:
                        skipped_no_bit += 1
                        logger.debug("Skip signal '%s' in '%s' — no free bit space", name, msg_name)
                        continue
                else:
                    sb = int(sb)
                    if not big_endian:
                        start_lsb = sb
                    else:
                        r, c = divmod(sb, 8)
                        start_lsb = (r * 8) + c - length + 1
                    if start_lsb < 0 or start_lsb + length > total_bits:
                        skipped_no_bit += 1
                        logger.warning(
                            "Skip signal '%s' in '%s' — start_lsb=%d out of frame [0, %d)",
                            name, msg_name, start_lsb, total_bits,
                        )
                        continue

                # Tính min/max khi thiếu
                sig_min = rs["minimum"]
                sig_max = rs["maximum"]
                if sig_min is None or sig_max is None:
                    if is_signed:
                        raw_lo = -(1 << (length - 1))
                        raw_hi = (1 << (length - 1)) - 1
                    else:
                        raw_lo = 0
                        raw_hi = (1 << length) - 1
                    phys_a = raw_lo * factor + offset
                    phys_b = raw_hi * factor + offset
                    phys_lo = min(phys_a, phys_b)
                    phys_hi = max(phys_a, phys_b)
                    if sig_min is None:
                        sig_min = phys_lo
                        auto_filled_range += 1
                    if sig_max is None:
                        sig_max = phys_hi
                        auto_filled_range += 1

                sig_min_f = float(sig_min)
                sig_max_f = float(sig_max)
                if sig_min_f > sig_max_f:
                    sig_min_f, sig_max_f = sig_max_f, sig_min_f

                parsed_sigs[name] = ParsedSignal(
                    name=name,
                    start_bit=sb,
                    length=length,
                    is_signed=is_signed,
                    byte_order="big_endian" if big_endian else "little_endian",
                    factor=factor,
                    offset=offset,
                    unit=rs["unit"],
                    minimum=sig_min_f,
                    maximum=sig_max_f,
                    description=rs["comment"],
                    db_source=resolved.name,
                    receivers=rs.get("receivers", []),
                )

            if parsed_sigs:
                pm = ParsedMessage(
                    msg_id=msg_id,
                    name=msg_name,
                    dlc=dlc,
                    senders=senders,
                    signals=parsed_sigs,
                    db_source=resolved.name,
                    description=description or "",
                )
                if msg_id in self._messages:
                    self._messages[msg_id].signals.update(parsed_sigs)
                else:
                    self._messages[msg_id] = pm
                for sig_name, sig in parsed_sigs.items():
                    if sig_name in self._signals:
                        logger.warning(
                            "Signal '%s' redefined (prev msg_id=%#x, "
                            "new msg_id=%#x) — overwriting",
                            sig_name,
                            self._signal_to_msg.get(sig_name, 0),
                            msg_id,
                        )
                    self._signals[sig_name] = sig
                    self._signal_to_msg[sig_name] = msg_id
                loaded_msg_count += 1

        self._loaded_files.append(str(resolved))
        logger.info(
            "can.json loaded: %s — %d messages, %d signals "
            "(skipped %d no-startbit, auto-filled %d ranges)",
            resolved.name,
            loaded_msg_count,
            len(self._signals),
            skipped_no_bit,
            auto_filled_range,
        )

    @property
    def messages(self) -> dict[int, ParsedMessage]:
        return self._messages

    @property
    def signals(self) -> dict[str, ParsedSignal]:
        return self._signals

    def decode_frame(self, msg_id: int, data: bytes) -> dict[str, float]:
        """Giải mã byte CAN thô → dict tín hiệu."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return {}
        return decode_frame_from_msg(msg, data)

    def encode_signal(self, signal_name: str, value: float) -> can.Message | None:
        """Tìm thông điệp chứa ``signal_name`` và mã hóa nó."""
        msg_id = self._signal_to_msg.get(signal_name)
        if msg_id is None:
            logger.debug("Signal not found in DB: %s", signal_name)
            return None
        msg = self._messages.get(msg_id)
        if msg is None:
            return None
        data = encode_frame_from_msg(msg, {signal_name: value})
        return can.Message(
            arbitration_id=msg.msg_id,
            data=data,
            is_extended_id=msg.msg_id > 0x7FF,
        )

    def encode_message(self, msg_id: int, signals: dict[str, float]) -> can.Message | None:
        """Mã hóa toàn bộ thông điệp theo ID với nhiều giá trị tín hiệu."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return None
        data = encode_frame_from_msg(msg, signals)
        return can.Message(
            arbitration_id=msg_id,
            data=data,
            is_extended_id=msg_id > 0x7FF,
        )

    def summary(self) -> str:
        return (
            f"DatabaseLoader: {len(self._loaded_files)} files loaded, "
            f"{len(self._messages)} messages, {len(self._signals)} signals"
        )
