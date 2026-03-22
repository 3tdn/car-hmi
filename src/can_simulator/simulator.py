"""Bộ điều khiển mô phỏng ECU — gửi khung CAN qua một bus ảo
và sử dụng bộ phân tích CAN DB để mã hóa/tạo khung.

Tệp này chứa hai dạng mô phỏng:
- `CANSimulator`: phát lại kịch bản (scenario) theo thời gian cố định.
- `RandomCANSimulator`: sinh giá trị tín hiệu ngẫu nhiên có ràng buộc.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import can

from src.can_io.parser import DatabaseLoader

logger = logging.getLogger(__name__)


@dataclass
class ScenarioStep:
    at_sec: float
    signals: dict[str, float]


@dataclass
class Scenario:
    name: str
    duration_sec: int
    steps: list[ScenarioStep] = field(default_factory=list)


class CANSimulator:
    """Mô phỏng ECU bằng cách phát các khung CAN theo kịch bản YAML.

     Trình tự hoạt động mỗi chu kỳ
     -----------------------------
     1. Tiến các bước kịch bản khi thời điểm ``at_sec`` đã đến.
     2. Với mỗi tín hiệu thay đổi, tra xem nó thuộc thông điệp (message)
         nào trong ``DatabaseLoader``.
     3. Mã hóa toàn bộ thông điệp (tất cả tín hiệu cho ``msg_id`` đó) và gửi đi.
     4. Ngủ tới chu kỳ tiếp theo.

    Dùng bus ảo của python-can nên không cần phần cứng khi phát triển.
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        scenario: Scenario,
        default_cycle_ms: int = 50,
        loop: bool = False,
    ) -> None:
        """
        Tham số:
            bus:              Thể hiện `can.BusABC` đã mở để truyền.
            db:               Đối tượng `DatabaseLoader` (cung cấp `encode_message`).
            scenario:         Kịch bản đã phân tích cú pháp để phát lại.
            default_cycle_ms: Thời gian chu kỳ TX (ms).
            loop:             Nếu True thì lặp lại kịch bản khi kết thúc.
        """
        self._bus = bus
        self._db = db
        self._scenario = scenario
        self._cycle_ms = default_cycle_ms
        self._loop = loop
        self._running = False

        # Tiền tính ánh xạ signal -> msg_id để tra cứu O(1)
        self._sig_to_msg_id: dict[str, int] = {}
        for msg in self._db.messages.values():
            for sig_name in msg.signals:
                self._sig_to_msg_id[sig_name] = msg.msg_id

    async def start(self) -> None:
        """Phát kịch bản: tiến bước, mã hóa khung và gửi mỗi chu kỳ."""
        self._running = True
        loop = asyncio.get_running_loop()
        logger.info(
            "CAN Simulator started (scenario=%s, duration=%ds, cycle=%dms, msgs=%d)",
            self._scenario.name,
            self._scenario.duration_sec,
            self._cycle_ms,
            len(self._db.messages),
        )

        while self._running:
            await self._run_once(loop)
            if self._loop:
                logger.info("Scenario looping...")
            else:
                break

        self._running = False
        logger.info("CAN Simulator stopped.")

    async def _run_once(self, loop: asyncio.AbstractEventLoop) -> None:
        start = time.monotonic()
        step_idx = 0
        current_signals: dict[str, float] = {}
        # Track which message IDs are dirty (need re-encoding)
        dirty_msg_ids: set[int] = set()

        while self._running:
            elapsed = time.monotonic() - start

            # Tiến các bước kịch bản
            while step_idx < len(self._scenario.steps):
                step = self._scenario.steps[step_idx]
                if elapsed >= step.at_sec:
                    for sig_name, value in step.signals.items():
                        current_signals[sig_name] = value
                        # Đánh dấu thông điệp chứa tín hiệu này là 'dirty' (cần mã hóa)
                        msg_id = self._sig_to_msg_id.get(sig_name)
                        if msg_id is not None:
                            dirty_msg_ids.add(msg_id)
                    step_idx += 1
                    logger.debug(
                        "Scenario step applied: t=%.1fs signals=%s",
                        elapsed,
                        list(step.signals.keys()),
                    )
                else:
                    break

            if elapsed >= self._scenario.duration_sec:
                logger.info("Scenario '%s' complete (%.1fs)", self._scenario.name, elapsed)
                break

            # Encode and send all dirty messages
            for msg_id in list(dirty_msg_ids):
                msg_def = self._db.messages.get(msg_id)
                if msg_def is None:
                    dirty_msg_ids.discard(msg_id)
                    continue
                # Thu thập tất cả giá trị tín hiệu cho thông điệp này.
                # Điền giá trị mặc định cho tín hiệu thiếu để tránh lỗi mã hóa
                # khi kịch bản không đặt mọi trường.
                msg_signals: dict[str, float] = {}
                for sig_name, sig_def in msg_def.signals.items():
                    if sig_name in current_signals:
                        msg_signals[sig_name] = current_signals[sig_name]
                    elif sig_def.minimum is not None:
                        msg_signals[sig_name] = sig_def.minimum
                    else:
                        msg_signals[sig_name] = 0.0

                try:
                    can_msg = self._db.encode_message(msg_id, msg_signals)
                    if can_msg is not None:
                        await loop.run_in_executor(None, self._bus.send, can_msg)
                        logger.debug("SIM TX: msg_id=%#x signals=%s", msg_id, msg_signals)
                except can.CanError as exc:
                    logger.error("Simulator TX error msg_id=%#x: %s", msg_id, exc)
                except Exception as exc:
                    logger.warning("Simulator encode error msg_id=%#x: %s", msg_id, exc)

            dirty_msg_ids.clear()
            await asyncio.sleep(self._cycle_ms / 1000.0)

    def stop(self) -> None:
        """Signal the simulation loop to stop after the current cycle."""
        self._running = False


class RandomCANSimulator:
    """Bộ mô phỏng sinh tín hiệu ngẫu nhiên.

    Tải message/tín hiệu từ ``DatabaseLoader`` và định kỳ cập nhật giá trị
    ngẫu nhiên. Các thay đổi được giới hạn sao cho độ lệch so với giá trị
    trước đó không vượt quá ``max_delta_percent``.

    Mã hóa toàn bộ thông điệp và gửi trên bus CAN để `CANReader` và pipeline
    có thể tiêu thụ và xử lý.
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        default_cycle_ms: int = 500,
        update_hz: float = 1.0,
        max_delta_percent: float = 10.0,
        loop: bool = True,
        min_value: float | None = None,
        max_value: float | None = None,
    ) -> None:
        self._bus = bus
        self._db = db
        self._cycle_ms = default_cycle_ms
        self._update_hz = float(update_hz)
        self._max_delta_pct = float(max_delta_percent)
        self._loop = loop
        self._min_value = None if min_value is None else float(min_value)
        self._max_value = None if max_value is None else float(max_value)
        self._running = False

        # Tiền tính các ánh xạ
        self._sig_to_msg_id: dict[str, int] = {}
        for msg in self._db.messages.values():
            for sig_name in msg.signals:
                self._sig_to_msg_id[sig_name] = msg.msg_id

        # Gom các tín hiệu theo thông điệp để mã hóa hiệu quả
        self._msg_to_signals: dict[int, list[str]] = {}
        for msg_id, msg in self._db.messages.items():
            self._msg_to_signals[msg_id] = list(msg.signals.keys())

        # Giá trị hiện tại cho mỗi tín hiệu (khởi tạo từ minimum/maximum DB
        # hoặc từ ngưỡng toàn cục nếu được cung cấp)
        self._current_values: dict[str, float] = {}
        for name, sig in self._db.signals.items():
            # Xác định min/max hiệu dụng: ưu tiên giá trị trong DBC, nếu không
            # có thì dùng giá trị ghi đè toàn cục của simulator nếu tồn tại.
            eff_min = sig.minimum if sig.minimum is not None else self._min_value
            eff_max = sig.maximum if sig.maximum is not None else self._max_value
            if eff_min is not None and eff_max is not None:
                self._current_values[name] = (eff_min + eff_max) / 2.0
            elif eff_min is not None:
                self._current_values[name] = float(eff_min)
            elif eff_max is not None:
                self._current_values[name] = float(eff_max)
            else:
                self._current_values[name] = 0.0

    async def start(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()
        logger.info(
            "Random CAN Simulator started (cycle=%dms, update_hz=%.2f, msgs=%d, sigs=%d)",
            self._cycle_ms,
            self._update_hz,
            len(self._msg_to_signals),
            len(self._current_values),
        )

        try:
            while self._running:
                await self._run_once(loop)
                if not self._loop:
                    break
        finally:
            self._running = False
            logger.info("Random CAN Simulator stopped.")

    async def _run_once(self, loop: asyncio.AbstractEventLoop) -> None:
        # Xác suất mỗi tín hiệu được cập nhật trong mỗi chu kỳ
        p = min(1.0, self._update_hz * (self._cycle_ms / 1000.0))

        dirty_msgs: set[int] = set()

        for sig_name in list(self._current_values.keys()):
            if random.random() >= p:  # noqa: S311
                continue
            prev = float(self._current_values[sig_name])
            # Tính delta tối đa so với giá trị trước
            max_delta = max(abs(prev), 1.0) * (self._max_delta_pct / 100.0)
            delta = random.uniform(-max_delta, max_delta)  # noqa: S311
            new_val = prev + delta

            # Giới hạn theo min/max chỉ định trong DBC nếu có
            sig_def = self._db.signals.get(sig_name)
            if sig_def is not None:
                eff_min = sig_def.minimum if sig_def.minimum is not None else self._min_value
                eff_max = sig_def.maximum if sig_def.maximum is not None else self._max_value
                if eff_min is not None:
                    new_val = max(new_val, float(eff_min))
                if eff_max is not None:
                    new_val = min(new_val, float(eff_max))

            self._current_values[sig_name] = new_val
            msg_id = self._sig_to_msg_id.get(sig_name)
            if msg_id is not None:
                dirty_msgs.add(msg_id)

        # Mã hóa và gửi thông điệp dirty
        for msg_id in list(dirty_msgs):
            msg_def = self._db.messages.get(msg_id)
            if msg_def is None:
                continue
            msg_signals: dict[str, float] = {}
            for sname in self._msg_to_signals.get(msg_id, []):
                if sname in self._current_values:
                    msg_signals[sname] = self._current_values[sname]
                else:
                    sd = msg_def.signals.get(sname)
                    if sd and sd.minimum is not None:
                        msg_signals[sname] = float(sd.minimum)
                    else:
                        msg_signals[sname] = 0.0

            try:
                can_msg = self._db.encode_message(msg_id, msg_signals)
                if can_msg is not None:
                    await loop.run_in_executor(None, self._bus.send, can_msg)
                    logger.debug(
                        "RANDOM SIM TX: msg_id=%#x signals=%s", msg_id, list(msg_signals.keys())
                    )
            except Exception as exc:
                logger.warning("Random simulator TX error msg_id=%#x: %s", msg_id, exc)

        await asyncio.sleep(self._cycle_ms / 1000.0)

    def stop(self) -> None:
        self._running = False
