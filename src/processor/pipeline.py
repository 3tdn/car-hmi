"""Pipeline xử lý tín hiệu — chuỗi các giai đoạn xử lý (mẫu Pipeline)."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ProcessingStage(ABC):
    """Lớp cơ sở trừu tượng cho một giai đoạn pipeline."""

    @abstractmethod
    async def process(self, signals: dict[str, float]) -> dict[str, float]: ...


class SignalPipeline:
    """Xử lý khung đã giải mã qua chuỗi các giai đoạn theo thứ tự.

    Tiêu thụ các đối tượng ``DecodedFrame`` từ ``asyncio.Queue``, chạy chúng
    qua tất cả ``ProcessingStage``, sau đó:
    - Phát giá trị đã xử lý lên ``SignalStore`` (trong bộ nhớ, thời gian thực)
    - Đệm giá trị cho lần ghi batch vào repository (SQLite)

    Flush theo lô
    ----------------
    Bản ghi được ghi vào storage khi một trong hai điều kiện xảy ra:
    - Bộ đệm đạt ``batch_size`` bản ghi, hoặc
    - Đã qua ``batch_interval_sec`` giây kể từ lần flush gần nhất.
    """

    def __init__(
        self,
        input_queue: asyncio.Queue,
        signal_store,
        repository,
        queue_policy: str = "reject",
        batch_size: int = 100,
        batch_interval_sec: float = 2.0,
        batch_drain_size: int = 200,
    ) -> None:
        self._queue = input_queue
        self._store = signal_store
        self._repo = repository
        self._policy = queue_policy
        self._batch_size = batch_size
        self._batch_interval = batch_interval_sec
        self._batch_drain_size = batch_drain_size
        self._stages: list[ProcessingStage] = []
        self._running = False
        self._buffer: list[tuple[str, float, str | None]] = []  # [(signal_name, value, unit), …]
        self._last_flush = time.monotonic()

    def add_stage(self, stage: ProcessingStage) -> None:
        self._stages.append(stage)

    async def start(self) -> None:
        self._running = True
        logger.info("Signal pipeline started (%d stages)", len(self._stages))
        while self._running:
            # --- 1. Chờ frame đầu tiên với timeout ---
            # Avoid asyncio.wait_for() — Python 3.10 has a known bug where
            # CancelledError is converted to TimeoutError and can escape the
            # except clause.  Using asyncio.wait() with FIRST_COMPLETED
            # sidesteps the issue entirely.
            frame = None
            try:
                get_coro = self._queue.get()
                get_task = asyncio.ensure_future(get_coro)
                done, _ = await asyncio.wait({get_task}, timeout=1.0)
                if done:
                    frame = get_task.result()
                else:
                    get_task.cancel()
                    # Suppress CancelledError from the cancelled task
                    try:
                        await get_task
                    except (asyncio.CancelledError, Exception):
                        pass
                    # Idle — flush buffer if interval elapsed
                    if time.monotonic() - self._last_flush >= self._batch_interval:
                        try:
                            await self._flush_buffer()
                        except Exception as fe:
                            logger.exception("Error flushing buffer during idle: %s", fe)
                    continue
            except asyncio.CancelledError:
                # Task is being shut down — exit cleanly
                logger.debug("Signal pipeline task cancelled, shutting down")
                return

            # --- 2. Drain toàn bộ queue: giữ giá trị mới nhất mỗi signal ---
            # Drain không giới hạn số frame cho đến khi queue rỗng.
            # Với tải cao, nhiều frame cùng signal_id nằm chờ trong queue;
            # merged.update() ghi đè liên tục → chỉ giá trị MỚI NHẤT được xử lý.
            # Đảm bảo tín hiệu cập nhật thường xuyên không tích lũy giá trị cũ
            # trong buffer storage hay SignalStore.
            merged: dict[str, float] = dict(frame.signals)
            drained = 1
            while True:
                try:
                    extra = self._queue.get_nowait()
                    merged.update(extra.signals)  # giá trị mới hơn ghi đè
                    drained += 1
                except asyncio.QueueEmpty:
                    break

            # --- 3. Xử lý batch đã merge ---
            try:
                await self._process_signals(merged)
            except Exception as exc:
                logger.exception("Pipeline error (dropping batch of %d): %s", drained, exc)

    async def _process_signals(self, signals: dict[str, float]) -> None:
        """Xử lý dict tín hiệu đã merge qua tất cả stages."""
        for stage in self._stages:
            try:
                signals = await stage.process(signals)
            except Exception as exc:
                logger.error("Stage %s failed: %s — dropping batch", type(stage).__name__, exc)
                return
            if not signals:
                logger.debug("Stage %s returned empty signals — dropping batch", type(stage).__name__)
                return

        now = time.time()
        # Phát lên SignalStore — bulk update: 1 lock thay vì N lock
        await self._store.bulk_update(signals, timestamp=now)

        # Đệm cho ghi batch vào storage — đọc unit đồng bộ để tránh N lần acquire asyncio.Lock
        for name, value in signals.items():
            unit = self._store.get_unit(name)
            self._buffer.append((name, value, unit))

        # Flush nếu đạt ngưỡng batch
        buffer_full = len(self._buffer) >= self._batch_size
        interval_elapsed = time.monotonic() - self._last_flush >= self._batch_interval
        if buffer_full or interval_elapsed:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        if not self._buffer:
            self._last_flush = time.monotonic()
            return
        items = self._buffer[:]
        self._buffer.clear()
        self._last_flush = time.monotonic()
        now = time.time()
        from src.storage.repository import SignalRecord

        records = [SignalRecord(name, value, unit, now) for name, value, unit in items]
        try:
            await self._repo.insert_signals_bulk(records)
        except Exception as exc:
            logger.warning("Storage bulk insert error: %s", exc)
        logger.debug("Flushed %d signal records to storage", len(items))

    async def flush(self) -> None:
        """Flush bộ đệm còn lại — gọi khi tắt giữ nãt."""
        logger.info("Final pipeline flush (%d records)...", len(self._buffer))
        await self._flush_buffer()

    def stop(self) -> None:
        self._running = False

    def set_input_queue(self, new_queue: asyncio.Queue) -> None:
        """Hoán đổi queue đầu vào của pipeline. Caller chịu trách nhiệm chuyển dữ liệu nếu cần."""
        self._queue = new_queue
