"""Signal processing pipeline — a sequence of processing stages (Pipeline pattern)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ProcessingStage(ABC):
    """Abstract base class for a pipeline stage."""

    @abstractmethod
    async def process(self, signals: dict[str, float]) -> dict[str, float]: ...


class SignalPipeline:
    """Process decoded frames and publish the latest values to SignalStore."""

    def __init__(
        self,
        input_queue: asyncio.Queue,
        signal_store,
        queue_policy: str = "reject",
        batch_drain_size: int = 200,
    ) -> None:
        self._queue = input_queue
        self._store = signal_store
        self._policy = queue_policy
        self._batch_drain_size = batch_drain_size
        self._stages: list[ProcessingStage] = []
        self._running = False

    def add_stage(self, stage: ProcessingStage) -> None:
        self._stages.append(stage)

    async def start(self) -> None:
        self._running = True
        logger.info("Signal pipeline started (%d stages)", len(self._stages))
        while self._running:
            frame = None
            try:
                get_task = asyncio.ensure_future(self._queue.get())
                done, _ = await asyncio.wait({get_task}, timeout=1.0)
                if done:
                    frame = get_task.result()
                else:
                    get_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await get_task
                    continue
            except asyncio.CancelledError:
                logger.debug("Signal pipeline task cancelled, shutting down")
                return

            # Drain a bounded batch and retain the newest value per signal.
            merged: dict[str, float] = dict(frame.signals)
            drained = 1
            while drained < self._batch_drain_size:
                try:
                    extra = self._queue.get_nowait()
                    merged.update(extra.signals)
                    drained += 1
                except asyncio.QueueEmpty:
                    break

            try:
                await self._process_signals(merged)
            except Exception as exc:
                logger.exception("Pipeline error (dropping batch of %d): %s", drained, exc)

    async def _process_signals(self, signals: dict[str, float]) -> None:
        """Process one merged signal dictionary through every configured stage."""
        for stage in self._stages:
            try:
                signals = await stage.process(signals)
            except Exception as exc:
                logger.error("Stage %s failed: %s — dropping batch", type(stage).__name__, exc)
                return
            if not signals:
                logger.debug(
                    "Stage %s returned empty signals — dropping batch",
                    type(stage).__name__,
                )
                return

        await self._store.bulk_update(signals, timestamp=time.time())

    def stop(self) -> None:
        self._running = False

    def set_input_queue(self, new_queue: asyncio.Queue) -> None:
        """Swap the pipeline input queue. The caller handles pending data migration."""
        self._queue = new_queue

    def apply_runtime_config(
        self,
        *,
        queue_policy: str,
        batch_drain_size: int,
    ) -> None:
        """Synchronize pipeline knobs that do not require task recreation."""
        self._policy = queue_policy
        self._batch_drain_size = int(batch_drain_size)
