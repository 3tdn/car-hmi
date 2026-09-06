"""Signal processing pipeline — a sequence of processing stages (Pipeline pattern)."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ProcessingStage(ABC):
    """Abstract base class for a pipeline stage."""

    @abstractmethod
    async def process(self, signals: dict[str, float]) -> dict[str, float]: ...


class SignalPipeline:
    """Process decoded frames through an ordered chain of stages.

    Consumes ``DecodedFrame`` objects from ``asyncio.Queue``, runs them
    through all ``ProcessingStage`` instances, then:
    - Publish processed values to ``SignalStore`` (in-memory, real-time)
    - Buffer values for batch writes to the repository (SQLite)

    Batch flushing
    --------------
    Records are written to storage when either of these conditions occurs:
    - The buffer reaches ``batch_size`` records, or
    - ``batch_interval_sec`` seconds have elapsed since the last flush.
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
            # --- 1. Wait for the first frame with a timeout ---
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

            # --- 2. Drain the entire queue: keep the latest value for each signal ---
            # Drain without a frame limit until the queue is empty.
            # Under high load, many frames for the same signal_id may be waiting in the queue;
            # merged.update() overwrites continuously → only the NEWEST value is processed.
            # This ensures frequently updated signals do not accumulate stale values
            # trong buffer storage hay SignalStore.
            merged: dict[str, float] = dict(frame.signals)
            drained = 1
            while True:
                try:
                    extra = self._queue.get_nowait()
                    merged.update(extra.signals)  # newer values overwrite older ones
                    drained += 1
                except asyncio.QueueEmpty:
                    break

            # --- 3. Process the merged batch ---
            try:
                await self._process_signals(merged)
            except Exception as exc:
                logger.exception("Pipeline error (dropping batch of %d): %s", drained, exc)

    async def _process_signals(self, signals: dict[str, float]) -> None:
        """Process the merged signal dict through all stages."""
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
        # Publish to SignalStore — bulk update: 1 lock instead of N locks
        await self._store.bulk_update(signals, timestamp=now)

        # Buffer for batch storage writes — read units synchronously to avoid N asyncio.Lock acquisitions
        for name, value in signals.items():
            unit = self._store.get_unit(name)
            self._buffer.append((name, value, unit))

        # Flush when the batch threshold is reached
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
        """Flush the remaining buffer — call this during graceful shutdown."""
        logger.info("Final pipeline flush (%d records)...", len(self._buffer))
        await self._flush_buffer()

    def stop(self) -> None:
        self._running = False

    def set_input_queue(self, new_queue: asyncio.Queue) -> None:
        """Swap the pipeline input queue. The caller is responsible for moving data if needed."""
        self._queue = new_queue
