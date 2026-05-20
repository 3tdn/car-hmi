"""CAN Simulator — gửi khung CAN ngẫu nhiên qua virtual bus từ config/can.json.

``CANSimulator`` đọc trực tiếp ``can.json``, sinh giá trị ngẫu nhiên trong
``[minimum, maximum]`` cho mỗi tín hiệu, mã hóa và gửi lên bus CAN ảo.
Không phụ thuộc DBC raw; mọi thông số lấy từ ``can.json``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import time

import can

from src.can_io.parser import _insert_bits

logger = logging.getLogger(__name__)


def _mark_used_bits(used: list[bool], start_lsb: int, length: int) -> None:
    """Đánh dấu các bit đã dùng trong bitmask `used` (LSB-indexed)."""
    total = len(used)
    for b in range(max(0, start_lsb), min(total, start_lsb + length)):
        used[b] = True


@dataclass
class _SigDef:
    """Định nghĩa tín hiệu đọc từ can.json."""

    name: str
    start_bit: int
    length: int
    is_signed: bool
    big_endian: bool
    factor: float
    offset: float
    minimum: float
    maximum: float


@dataclass
class _MsgDef:
    """Định nghĩa thông điệp đọc từ can.json."""

    msg_id: int
    name: str
    dlc: int
    signals: list[_SigDef] = field(default_factory=list)


class CANSimulator:
    """Bộ mô phỏng đọc định nghĩa message/signal trực tiếp từ ``can.json``
    và sinh giá trị ngẫu nhiên đều trong ``[minimum, maximum]`` cho từng tín hiệu.

    Không phụ thuộc vào file DBC raw — mọi thông số tín hiệu
    lấy từ ``can.json``, thông số bus và chu kỳ lấy từ ``AppConfig``.

    Các tín hiệu bị trống ``start_bit`` hoặc thiếu ``minimum``/``maximum`` sẽ lấy giá trị hơp lý:
    - ``start_bit`` là ``null`` sẽ được tự động phân bổ vào vị trí bit trống đầu tiên trong message.
    - ``minimum`` hoặc ``maximum`` là ``null`` sẽ được tính dựa trên độ dài bit, signedness, factor và offset.
    """

    def __init__(
        self,
        bus: can.BusABC,
        can_json_path: str | Path,
        cycle_ms: int = 50,
        repeat: bool = True,
    ) -> None:
        """
        Tham số:
            bus:           Thể hiện ``can.BusABC`` đã mở để truyền.
            can_json_path: Đường dẫn tới file ``can.json``.
            cycle_ms:      Chu kỳ phát (ms) — lấy từ ``cfg.simulator.default_cycle_ms``.
            repeat:        Nếu ``True`` thì chạy liên tục cho đến khi ``stop()``.
        """
        self._bus = bus
        self._cycle_ms = cycle_ms
        self._repeat = repeat
        self._running = False
        self._messages = self._load_can_json(Path(can_json_path))
        logger.info(
            "CANSimulator initialized: %d messages, %d signals total from '%s'",
            len(self._messages),
            sum(len(m.signals) for m in self._messages),
            can_json_path,
        )

    # ── Tải can.json ───────────────────────────────────────────────────────────

    def _load_can_json(self, path: Path) -> list[_MsgDef]:
        if not path.exists():
            raise FileNotFoundError(f"can.json not found: {path}")
        data_json: dict = json.loads(path.read_text(encoding="utf-8"))
        result: list[_MsgDef] = []
        skipped_no_bit = 0
        auto_filled_range = 0
        for msg_name, md in data_json.get("messages", {}).items():
            # safe key access for required fields
            raw_id = md.get("id")
            if raw_id is None:
                logger.warning("Skip message '%s' — missing 'id' field", msg_name)
                continue
            msg_id = int(raw_id)
            dlc = int(md.get("size", md.get("dlc", 8)))
            sigs: list[_SigDef] = []
            # Collect raw signal dicts first so we can allocate missing start_bit
            raw_sigs: list[dict] = []
            for sig_name, sd in md.get("signals", {}).items():
                # skip TX signals — những tín hiệu CAR_PC ghi ra bus, không mô phỏng
                if sd.get("TX", False):
                    logger.debug("Skip TX signal '%s' in '%s' (simulator only sends ECU→CarPC)", sig_name, msg_name)
                    continue
                # skip signals missing 'length'
                raw_len = sd.get("length")
                if raw_len is None:
                    logger.warning("Skip signal '%s' in '%s' — missing 'length'", sig_name, msg_name)
                    continue
                length = int(raw_len)
                # guard length <= 0
                if length <= 0:
                    logger.warning("Skip signal '%s' in '%s' — invalid length=%d", sig_name, msg_name, length)
                    continue
                # guard factor == 0
                factor = float(sd.get("factor", 1.0))
                if factor == 0.0:
                    logger.warning(
                        "Signal '%s' in '%s' has factor=0, defaulting to 1.0", sig_name, msg_name
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
                    "minimum": sd.get("minimum"),
                    "maximum": sd.get("maximum"),
                })

            # Track used bits (LSB-indexed 0..dlc*8-1). First pass: mark signals with known start_bit.
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
                # skip marking if start_lsb would be invalid
                if start_lsb < 0 or start_lsb + rs["length"] > total_bits:
                    logger.debug(
                        "Sig '%s' in '%s': start_lsb=%d out of range [0, %d), skipping bit-mark",
                        rs["name"], msg_name, start_lsb, total_bits,
                    )
                    continue
                _mark_used_bits(used, start_lsb, rs["length"])

            # Second pass: build _SigDef, allocating defaults where missing
            for rs in raw_sigs:
                name = rs["name"]
                length = rs["length"]
                is_signed = rs["is_signed"]
                big_endian = rs["big_endian"]
                factor = rs["factor"]
                offset = rs["offset"]

                # Allocate start_bit if missing: find first contiguous free run
                sb = rs["start_bit"]
                if sb is None:
                    found = False
                    for p in range(0, total_bits - length + 1):
                        if all(not used[b] for b in range(p, p + length)):
                            # for big_endian, canonical start_bit = MSB position
                            assigned_sb = p + length - 1 if big_endian else p
                            rs["start_bit"] = assigned_sb
                            _mark_used_bits(used, p, length)
                            found = True
                            logger.debug(
                                "Auto-assign start_bit=%d for sig '%s' in '%s' (len=%d, %s)",
                                assigned_sb, name, msg_name, length,
                                "big_endian" if big_endian else "little_endian",
                            )
                            break
                    if not found:
                        skipped_no_bit += 1
                        logger.debug("Skip signal '%s' in '%s' — no free bit space", name, msg_name)
                        continue
                else:
                    rs["start_bit"] = int(rs["start_bit"])
                    # validate start_lsb is in range before accepting the signal
                    if not big_endian:
                        start_lsb = rs["start_bit"]
                    else:
                        r, c = divmod(rs["start_bit"], 8)
                        start_lsb = (r * 8) + c - length + 1
                    if start_lsb < 0 or start_lsb + length > total_bits:
                        skipped_no_bit += 1
                        logger.warning(
                            "Skip signal '%s' in '%s' — start_lsb=%d out of frame [0, %d)",
                            name, msg_name, start_lsb, total_bits,
                        )
                        continue

                # compute physical range respecting factor sign;
                # always ensure sig_min <= sig_max
                sig_min = rs["minimum"]
                sig_max = rs["maximum"]
                if sig_min is None or sig_max is None:
                    if is_signed:
                        raw_lo = -(1 << (length - 1))
                        raw_hi = (1 << (length - 1)) - 1
                    else:
                        raw_lo = 0
                        raw_hi = (1 << length) - 1
                    # factor may be negative — use min/max to keep lo <= hi
                    phys_a = raw_lo * factor + offset
                    phys_b = raw_hi * factor + offset
                    phys_lo = min(phys_a, phys_b)
                    phys_hi = max(phys_a, phys_b)
                    if sig_min is None and sig_max is None:
                        sig_min = phys_lo
                        sig_max = phys_hi
                        auto_filled_range += 1
                        logger.debug(
                            "Auto-fill min/max for sig '%s' in '%s': [%s, %s]",
                            name, msg_name, sig_min, sig_max,
                        )
                    elif sig_min is None:
                        sig_min = phys_lo
                        auto_filled_range += 1
                    else:
                        sig_max = phys_hi
                        auto_filled_range += 1

                # ensure min <= max after any combination of provided / auto values
                sig_min_f = float(sig_min)
                sig_max_f = float(sig_max)
                if sig_min_f > sig_max_f:
                    logger.warning(
                        "Signal '%s' in '%s': min(%s) > max(%s) — swapping",
                        name, msg_name, sig_min_f, sig_max_f,
                    )
                    sig_min_f, sig_max_f = sig_max_f, sig_min_f

                sigs.append(
                    _SigDef(
                        name=name,
                        start_bit=int(rs["start_bit"]),
                        length=length,
                        is_signed=is_signed,
                        big_endian=big_endian,
                        factor=factor,
                        offset=offset,
                        minimum=sig_min_f,
                        maximum=sig_max_f,
                    )
                )
            if sigs:
                result.append(_MsgDef(msg_id=msg_id, name=msg_name, dlc=dlc, signals=sigs))
        logger.info(
            "can.json loaded: %d messages usable, skipped %d no-startbit, auto-filled range for %d signals",
            len(result),
            skipped_no_bit,
            auto_filled_range,
        )
        return result

    # ── Vòng phát ──────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Bắt đầu phát tín hiệu ngẫu nhiên theo chu kỳ ``cycle_ms``."""
        self._running = True
        event_loop = asyncio.get_running_loop()
        logger.info(
            "CANSimulator started (cycle=%dms, msgs=%d)",
            self._cycle_ms,
            len(self._messages),
        )
        try:
            while self._running:
                await self._tick(event_loop)
                if not self._repeat:
                    break
        finally:
            self._running = False
            logger.info("CANSimulator stopped.")

    async def _tick(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """Một chu kỳ: sinh ngẫu nhiên, mã hóa và gửi tất cả message.

        đo thời gian gửi thực tế rồi sleep phần còn lại của chu kỳ
        để tránh drift (actual_period = send_time + sleep thay vì cycle_ms).
        """
        t_start = time.monotonic()
        for msg_def in self._messages:
            frame_data = bytearray(msg_def.dlc)
            for sig in msg_def.signals:
                physical = random.uniform(sig.minimum, sig.maximum)  # noqa: S311
                raw_val = round((physical - sig.offset) / sig.factor)
                # Clamp raw về dải bit hợp lệ
                if sig.is_signed:
                    r_min = -(1 << (sig.length - 1))
                    r_max = (1 << (sig.length - 1)) - 1
                else:
                    r_min = 0
                    r_max = (1 << sig.length) - 1
                raw_val = max(r_min, min(r_max, raw_val))
                try:
                    _insert_bits(frame_data, raw_val, sig.start_bit, sig.length, sig.is_signed, sig.big_endian)
                except Exception as exc:
                    logger.debug(
                        "CANSimulator encode error sig=%s msg=%s: %s",
                        sig.name, msg_def.name, exc,
                    )
            # auto-detect extended frame for IDs > 0x7FF
            can_msg = can.Message(
                arbitration_id=msg_def.msg_id,
                data=bytes(frame_data),
                is_extended_id=msg_def.msg_id > 0x7FF,
            )
            try:
                await event_loop.run_in_executor(None, self._bus.send, can_msg)
                logger.debug("SIM TX: msg_id=%#x (%s)", msg_def.msg_id, msg_def.name)
            except can.CanError as exc:
                logger.error(
                    "CANSimulator TX error msg_id=%#x (%s): %s",
                    msg_def.msg_id, msg_def.name, exc,
                )
        # sleep only the remaining time in the cycle
        elapsed = time.monotonic() - t_start
        remaining = self._cycle_ms / 1000.0 - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def stop(self) -> None:
        """Dừng vòng phát sau chu kỳ hiện tại."""
        self._running = False
