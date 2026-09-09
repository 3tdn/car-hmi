"""CAN database parser — loads from a DBC file or the legacy can.json file.

Responsibilities
----------------
- Load message/signal definitions directly from a ``.dbc`` file (via cantools),
  or from the legacy can.json export
- Automatically allocate ``start_bit`` when the value is ``null``
- Automatically compute ``minimum``/``maximum`` when missing
- Decode raw CAN frames into ``dict[signal_name, float]``
- Encode signal values into ``can.Message``
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import can

logger = logging.getLogger(__name__)


# ── Data domain models ──────────────────────────────────────────────────────


@dataclass
class ParsedSignal:
    """Parsed signal information loaded from a DBC / CANdb file."""

    name: str
    start_bit: int
    length: int
    is_signed: bool
    byte_order: str  # "little_endian" | "big_endian" (byte order)
    factor: float
    offset: float
    unit: str
    minimum: float | None
    maximum: float | None
    description: str
    db_source: str  # source file from which the signal was loaded
    receivers: list[str] = field(default_factory=list)
    states: list[dict] = field(default_factory=list)  # enum states [{value, description}], if any


@dataclass
class ParsedMessage:
    """Parsed message information loaded from a DBC / CANdb file."""

    msg_id: int
    name: str
    dlc: int
    senders: list[str]
    signals: dict[str, ParsedSignal]  # signal_name → ParsedSignal (signal mapping)
    db_source: str
    cycle_ms: int | None = None
    description: str = ""


# ── DBC naming / comment conventions (shared by DatabaseLoader and CANSimulator) ──


def normalize_signal_name(name: str) -> str:
    """Canonical signal name: drop trailing lowercase suffixes (_bool, _status, _flag, _kmh, ...)."""
    name = (name or "").strip()
    return re.sub(r"_[a-z]\w*$", "", name) if name else ""


def split_comment_states(comment: str) -> tuple[str, list[dict]]:
    """Split a DBC signal comment into ``(clean_description, states)``.

    Recognizes the project convention (see ``scripts/dbc_utils.py``), e.g.:
    ``"Main comment | Signalvalues: 0: Off, 1: On"`` or ``"... | Signalvalues: level 1-10 x"``.
    """
    if not comment or "Signalvalues:" not in comment or "bit encoding" in comment.lower():
        return comment or "", []
    main_comment, states_part = comment.split("Signalvalues:", 1)
    main_comment = main_comment.rstrip(" ").rstrip("|").strip()
    return main_comment, _parse_states_from_comment(states_part.strip())


def _parse_states_from_comment(states_part: str) -> list[dict]:
    """Parse enum states out of a ``Signalvalues:`` string (see ``split_comment_states``)."""
    states: list[dict] = []
    if not states_part:
        return states
    parts = re.split(r"[;,]|\n", states_part)
    if len(parts) == 1:
        description = parts[0].replace("0-max ", "").rstrip().rstrip(".").strip()
        return [{"value": 0, "description": description}]
    idx = 0
    for part in parts:
        part = part.rstrip().rstrip(".").strip()
        if re.search(r"\s+[:\-=]\s+", part):
            val_str, desc = re.split(r"\s+[:\-=]\s+", part, maxsplit=1)
            val_str = val_str.strip()
            try:
                val = int(val_str)
            except ValueError:
                try:
                    val = float(val_str)
                except ValueError:
                    val = val_str  # keep as string if not int or float
            states.append({"value": val, "description": desc})
        elif re.search(r"\d+-\d+", part):
            m = re.match(r"(.*?)(\d+)-(\d+)(.*)", part)
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


def states_as_ints(states: list[dict]) -> list[int]:
    """Extract numeric ``value``s from a states list, skipping non-numeric entries."""
    result: list[int] = []
    for s in states:
        try:
            result.append(int(s["value"]))
        except (TypeError, ValueError, KeyError):
            continue
    return result


# ── Bit-manipulation helpers ───────────────────────────────────────────────


def _mark_used_bits(used: list[bool], start_lsb: int, length: int) -> None:
    """Mark bits as used in the ``used`` bitmask (LSB-indexed)."""
    total = len(used)
    for b in range(max(0, start_lsb), min(total, start_lsb + length)):
        used[b] = True


def _extract_bits(
    data: bytes, start_bit: int, length: int, is_signed: bool, big_endian: bool
) -> int:
    """Extract a value from CAN frame payload bytes.

    Uses the Intel (little-endian) bit-numbering convention.
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
    """Insert a raw integer value into a bytearray at the specified bit position."""
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


# ── Frame decoding / encoding ─────────────────────────────────────────────


def decode_frame_from_msg(msg: ParsedMessage, data: bytes) -> dict[str, float]:
    """Decode raw CAN bytes into a signal dict using factor/offset conversion."""
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
    """Encode a signal-value dict into CAN frame payload bytes."""
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


# ── DatabaseLoader — load from a DBC file or can.json ─────────────────────


class DatabaseLoader:
    """Load a CAN database directly from a ``.dbc`` file, or from the legacy ``can.json``.

    Supports:
    - Automatically allocating ``start_bit`` when the value is ``null``
    - Automatically computing ``minimum``/``maximum`` when missing
    - Decoding / encoding CAN frames through factor/offset conversion

    Usage::

        loader = DatabaseLoader()
        loader.load_dbc("db/can_db/p_v2.dbc")   # preferred: read directly from DBC
        loader.load("config/can.json")           # legacy: read from can.json export
        messages = loader.messages   # dict[msg_id → ParsedMessage]
        signals  = loader.signals    # dict[signal_name → ParsedSignal]
    """

    def __init__(self) -> None:
        self._messages: dict[int, ParsedMessage] = {}
        self._signals: dict[str, ParsedSignal] = {}
        self._signal_to_msg: dict[str, int] = {}  # signal_name → msg_id
        self._loaded_files: list[str] = []

    # ── Public API ───────────────────────────────────────────────────────────

    def load(self, path: str | Path) -> None:
        """Load can.json and merge message/signal definitions."""
        resolved = Path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"can.json not found: {resolved}")
        try:
            raw: dict = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"JSON parse failed {resolved}: {exc}") from exc

        def _iter_messages():
            for msg_name, md in raw.get("messages", {}).items():
                raw_id = md.get("id")
                if raw_id is None:
                    logger.warning("Skip message '%s' — missing 'id' field", msg_name)
                    continue
                msg_id = int(raw_id)
                dlc = int(md.get("size", md.get("dlc", 8)))
                senders = md.get("senders", [])
                description = md.get("comment", md.get("description", ""))

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
                        "states": sd.get("states", []),
                    })
                yield msg_name, msg_id, dlc, senders, description, raw_sigs

        self._ingest(_iter_messages(), resolved.name)
        self._loaded_files.append(str(resolved))

    def load_dbc(self, path: str | Path) -> None:
        """Load message/signal definitions directly from a DBC file (via cantools).

        Bypasses the can.json export step entirely — bit layout, factor/offset and
        min/max are read straight from the DBC and fed through the same bit-allocation
        / range auto-fill pipeline used by :meth:`load`.
        """
        resolved = Path(path)
        if not resolved.exists():
            raise FileNotFoundError(f"DBC file not found: {resolved}")
        try:
            import cantools
        except ImportError as exc:
            raise RuntimeError(
                "cantools is required to load DBC files (pip install cantools)"
            ) from exc
        try:
            db = cantools.database.load_file(str(resolved))
        except Exception as exc:
            raise ValueError(f"DBC parse failed {resolved}: {exc}") from exc

        def _num(value: object, default: float = 0.0) -> float:
            if value is None:
                return float(default)
            if hasattr(value, "item"):  # numpy scalar
                try:
                    return float(value.item())  # type: ignore[attr-defined]
                except Exception:
                    pass
            try:
                return float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return float(default)

        def _iter_messages():
            for msg in getattr(db, "messages", []):
                msg_id = int(msg.frame_id)
                dlc = int(getattr(msg, "length", 8) or 8)
                senders = list(getattr(msg, "senders", []) or [])
                description = getattr(msg, "comment", None) or ""
                msg_name = msg.name or f"{msg_id:#x}"

                raw_sigs: list[dict] = []
                for sig in getattr(msg, "signals", []):
                    sig_name = normalize_signal_name(getattr(sig, "name", ""))
                    if not sig_name:
                        continue
                    length = int(getattr(sig, "length", 0) or 0)
                    if length <= 0:
                        logger.warning(
                            "Skip signal '%s' in '%s' — invalid length", sig_name, msg_name,
                        )
                        continue
                    factor = _num(getattr(sig, "scale", getattr(sig, "factor", 1.0)), 1.0)
                    if factor == 0.0:
                        logger.warning(
                            "Signal '%s' in '%s' has factor=0, defaulting to 1.0",
                            sig_name, msg_name,
                        )
                        factor = 1.0
                    start_bit = getattr(sig, "start", None)
                    minimum = getattr(sig, "minimum", None)
                    maximum = getattr(sig, "maximum", None)
                    comment = getattr(sig, "comment", None) or ""
                    if isinstance(comment, dict):
                        comment = next(iter(comment.values()), "")
                    comment, states = split_comment_states(comment)
                    unit = getattr(sig, "unit", None)
                    if not unit and len(states) == 1:
                        # a lone "state" with no numeric enum is really just a unit annotation
                        unit = (states[0].get("description") or "").replace("0-max ", "")
                        states = []
                    raw_sigs.append({
                        "name": sig_name,
                        "start_bit": int(start_bit) if start_bit is not None else None,
                        "length": length,
                        "is_signed": bool(getattr(sig, "is_signed", False)),
                        "big_endian": getattr(sig, "byte_order", "little_endian") == "big_endian",
                        "factor": factor,
                        "offset": _num(getattr(sig, "offset", 0.0), 0.0),
                        "minimum": _num(minimum) if minimum is not None else None,
                        "maximum": _num(maximum) if maximum is not None else None,
                        "unit": unit or "",
                        "comment": comment,
                        "receivers": list(getattr(sig, "receivers", []) or []),
                        "states": states,
                    })
                yield msg_name, msg_id, dlc, senders, description, raw_sigs

        self._ingest(_iter_messages(), resolved.name)
        self._loaded_files.append(str(resolved))

    def _ingest(self, messages_iter, source_name: str) -> None:
        """Shared bit-allocation / range auto-fill pipeline for JSON and DBC sources."""
        skipped_no_bit = 0
        auto_filled_range = 0
        loaded_msg_count = 0

        for msg_name, msg_id, dlc, senders, description, raw_sigs in messages_iter:
            # Find used bits (LSB-indexed) — pass 1
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

            # Pass 2: build ParsedSignal objects and allocate start_bit when null
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
                    # Automatic allocation: find the first free bit range
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

                # Compute min/max when missing
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
                    db_source=source_name,
                    receivers=rs.get("receivers", []),
                    states=rs.get("states", []),
                )

            if parsed_sigs:
                pm = ParsedMessage(
                    msg_id=msg_id,
                    name=msg_name,
                    dlc=dlc,
                    senders=senders,
                    signals=parsed_sigs,
                    db_source=source_name,
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

        logger.info(
            "%s loaded: %d messages, %d signals "
            "(skipped %d no-startbit, auto-filled %d ranges)",
            source_name,
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
        """Decode raw CAN bytes into a signal dict."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return {}
        return decode_frame_from_msg(msg, data)

    def encode_signal(self, signal_name: str, value: float) -> can.Message | None:
        """Find the message containing ``signal_name`` and encode it."""
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
        """Encode an entire message by ID with multiple signal values."""
        msg = self._messages.get(msg_id)
        if msg is None:
            return None
        data = encode_frame_from_msg(msg, signals)
        return can.Message(
            arbitration_id=msg_id,
            data=data,
            is_extended_id=msg_id > 0x7FF,
        )

    def get_message_for_signal(self, signal_name: str) -> "ParsedMessage | None":
        """Return the ParsedMessage containing signal_name, or None if not found."""
        msg_id = self._signal_to_msg.get(signal_name)
        return self._messages.get(msg_id) if msg_id is not None else None

    def summary(self) -> str:
        return (
            f"DatabaseLoader: {len(self._loaded_files)} files loaded, "
            f"{len(self._messages)} messages, {len(self._signals)} signals"
        )
