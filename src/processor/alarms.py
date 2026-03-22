"""Giai đoạn phát hiện cảnh báo — kiểm tra giá trị tín hiệu so với ngưỡng cấu hình."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from src.processor.pipeline import ProcessingStage

logger = logging.getLogger(__name__)


@dataclass
class AlarmConfig:
    signal: str
    critical_high: float | None = None
    warning_high: float | None = None
    warning_low: float | None = None
    critical_low: float | None = None


@dataclass
class Alarm:
    signal: str
    level: str  # info | warning | critical
    value: float
    threshold: float
    timestamp: float
    description: str = ""


class AlarmChecker(ProcessingStage):
    """Kiểm tra từng giá trị tín hiệu so với ngưỡng cảnh báo.

    Phát ra đối tượng Alarm tới các handler đã đăng ký (v.d. SignalStore, Storage).
    """

    def __init__(self, configs: list[AlarmConfig]) -> None:
        self._configs = {c.signal: c for c in configs}
        self._alarm_handlers: list = []
        # Theo dõi trạng thái cảnh báo hiện tại cho mỗi tín hiệu để chỉ phát
        # khi có thay đổi (ví dụ: none -> warning, warning -> critical,
        # critical -> none, ...)
        # Lưu dạng: signal -> (level: str, threshold: float)
        self._last_state: dict[str, tuple[str, float] | None] = {}

    def add_alarm_handler(self, handler) -> None:
        self._alarm_handlers.append(handler)

    async def process(self, signals: dict[str, float]) -> dict[str, float]:
        for name, value in signals.items():
            cfg = self._configs.get(name)
            if cfg is None:
                continue
            # Tính trạng thái cảnh báo hiện tại (None hoặc (level, threshold))
            current = self._eval_state(cfg, value)
            last = self._last_state.get(name)
            # Nếu trạng thái thay đổi, phát ra một Alarm (hoặc thông tin giải quyết)
            if current != last:
                self._last_state[name] = current
                if current is None:
                    # Trạng thái chuyển về bình thường — phát thông tin giải quyết
                    ts = time.time()
                    alarm = Alarm(
                        name,
                        "info",
                        value,
                        last[1] if last is not None else 0.0,
                        ts,
                        f"{name} returned to normal",
                    )
                else:
                    level, threshold = current
                    ts = time.time()
                    alarm = Alarm(
                        name,
                        level,
                        value,
                        threshold,
                        ts,
                        f"{name} {('exceeded' if level in ('warning','critical') else 'changed')} threshold ({threshold})",
                    )
                for handler in self._alarm_handlers:
                    await handler(alarm)
        return signals  # pass-through: không sửa đổi giá trị

    def _check(self, cfg: AlarmConfig, name: str, value: float) -> Alarm | None:
        # Đã thay thế bởi _eval_state; giữ để tương thích nhưng không dùng nữa.
        return None

    def _eval_state(self, cfg: AlarmConfig, value: float) -> tuple[str, float] | None:
        """Đánh giá trạng thái cảnh báo hiện tại theo cấu hình.

        Trả về None nếu không có cảnh báo, hoặc (level, threshold) nếu đang ở
        trạng thái cảnh báo (ví dụ ('warning', 50.0) hoặc ('critical', 100.0)).
        """
        # Ưu tiên cảnh báo critical trước warning
        if cfg.critical_high is not None and value >= cfg.critical_high:
            return ("critical", cfg.critical_high)
        if cfg.critical_low is not None and value <= cfg.critical_low:
            return ("critical", cfg.critical_low)
        if cfg.warning_high is not None and value >= cfg.warning_high:
            return ("warning", cfg.warning_high)
        if cfg.warning_low is not None and value <= cfg.warning_low:
            return ("warning", cfg.warning_low)
        return None
