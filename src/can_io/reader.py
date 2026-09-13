"""Asynchronous CAN frame reader with full decoding support (DBC / CANdb JSON / A2L)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import can

from src.can_io.parser import DatabaseLoader

logger = logging.getLogger(__name__)

# Minimum interval for logging dropped-frame warnings (avoid spam).
_DROP_WARN_INTERVAL_SEC = 5.0


@dataclass
class RawCANFrame:
    timestamp: float
    bus: str
    msg_id: int
    is_extended: bool
    is_fd: bool
    data: bytes


@dataclass
class DecodedFrame:
    raw: RawCANFrame
    signals: dict[str, float] = field(default_factory=dict)
    msg_name: str = ""


class CANReader:
    """Asynchronously read CAN frames and decode them via DatabaseLoader.

    Decoded frames are pushed into an asyncio.Queue for SignalPipeline.
    Supports:
    - Allowlist filtering by CAN ID (optional)
    - Automatic reconnection on bus errors or stale traffic (staged backoff up to 1 hour)
    - A callback for cleanly reopening the bus during reconnect
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        queue: asyncio.Queue[DecodedFrame],
        filter_ids: set[int] | None = None,
        bus_factory: Callable[[], can.BusABC] | None = None,
        max_reconnect_retries: int = 5,
        queue_policy: str = "reject",
        max_rate_hz: float = 0.0,
        priority_sec: float = 0.0,
        stale_threshold_sec: float = 30.0,
        on_bus_reconnected: Callable[[can.BusABC], Awaitable[None]] | None = None,
    ) -> None:
        """
        Args:
            bus:            An open ``can.Bus`` object.
            db:             ``DatabaseLoader`` with loaded message/signal definitions.
            queue:          Output queue for decoded frames.
            filter_ids:     If not empty, process only frames with these IDs.
            bus_factory:    Callable used to reopen the bus after disconnection (optional).
            max_reconnect_retries: Maximum number of consecutive reconnect attempts.
            max_rate_hz:    If > 0, rate-gate each msg_id — drop a frame if
                            the same ID was just enqueued within 1/max_rate_hz seconds.
                            Helps prevent queue overflows when the simulator sends faster
                            than the pipeline can consume.
            priority_sec:   If > 0, signals not enqueued within this
                            time window are forced into the queue even if their value is unchanged.
                            Ensures low-frequency-changing signals are not missed.
            stale_threshold_sec: Reopen the bus if no frame arrives within this
                            duration. Set to 0 to disable stale-bus recovery.
            on_bus_reconnected: Awaited after a replacement bus opens, so paired
                            CAN writers can switch to the same bus instance.
        """
        self._bus = bus
        self._db = db
        self._queue = queue
        self._filter_ids = filter_ids or set()
        self._bus_factory = bus_factory
        self._max_retries = max_reconnect_retries
        self._policy = queue_policy
        self._running = False
        self._error_count = 0
        self._dropped_count = 0
        self._rate_limited_count = 0
        self._last_frame_timestamp: float = 0.0
        self._last_recv_monotonic: float = 0.0
        self._bus_opened_monotonic: float = 0.0
        self._fatal_error: str | None = None
        self._last_error: str | None = None
        # Per-ID rate gate: min interval in seconds (0 = disabled)
        self._min_interval = (1.0 / max_rate_hz) if max_rate_hz > 0 else 0.0
        self._last_enqueue: dict[int, float] = {}
        # Dedup: last raw data per msg_id; last decoded value per signal name
        self._last_msg_data: dict[int, bytes] = {}
        self._last_signal_values: dict[str, float] = {}
        # Priority: force-refresh signals that haven't been enqueued for > _priority_sec
        # 0.0 = disabled (only enqueue on value change)
        self._priority_sec: float = priority_sec
        self._stale_threshold_sec = max(0.0, stale_threshold_sec)
        self._on_bus_reconnected = on_bus_reconnected
        self._signal_last_enqueue_time: dict[str, float] = {}
        # Throttled drop warning state
        self._drop_warn_count = 0
        self._drop_warn_last_log = 0.0
        # Dedicated recv thread (set in start())
        self._recv_thread: threading.Thread | None = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnecting = False
        self._reconnect_attempt = 0
        # Cross-thread ingress coalescing. At most one event-loop callback may
        # be outstanding, while the latest signal values are retained per CAN
        # message. This bounds pending work by the number of active CAN IDs
        # instead of the number of received frames.
        self._pending_lock = threading.Lock()
        self._pending_frames: dict[int, DecodedFrame] = {}
        self._drain_scheduled = False
        self._received_count = 0
        self._coalesced_count = 0
        self._ingress_callbacks_scheduled = 0

    async def start(self) -> None:
        """Start reading CAN frames — runs until ``stop()`` is called.

        Architecture:
        - A dedicated thread calls ``bus.recv()`` in a tight loop (no asyncio overhead per frame).
        - Frames are filtered/deduplicated in the recv thread.
        - Changed values are coalesced per CAN ID behind at most one pending
          ``call_soon_threadsafe`` callback.
        - This bounds event-loop ingress independently of the raw frame rate.
        - Suitable for rates of 5,000-10,000 frames/s on 8 Mbps CAN FD.
        """
        self._running = True
        self._fatal_error = None
        event_loop = asyncio.get_running_loop()
        self._event_loop = event_loop
        self._bus_opened_monotonic = time.monotonic()
        logger.info(
            "CAN Reader started (interface=%s, channel=%s, %d msgs in DB)",
            getattr(self._bus, "INTERFACE", "?"),
            getattr(self._bus, "channel", "?"),
            len(self._db.messages),
        )
        self._recv_thread = self._spawn_recv_thread(event_loop)
        try:
            while self._running:
                await asyncio.sleep(0.5)
                # Watchdog: if the thread dies unexpectedly, try reconnecting
                if (
                    not self._recv_thread.is_alive()
                    and self._running
                    and not self._reconnecting
                ):
                    logger.warning("CAN recv thread exited unexpectedly — reconnecting...")
                    await self.request_reconnect()
                elif not self._reconnecting and self._is_bus_stale():
                    logger.warning(
                        "CAN bus has been silent for %.1fs — closing and reconnecting",
                        self._bus_silence_age_sec() or 0.0,
                    )
                    await self.request_reconnect()
        finally:
            self._running = False
            if self._reconnect_task and not self._reconnect_task.done():
                self._reconnect_task.cancel()
            if self._recv_thread and self._recv_thread.is_alive():
                # Offload blocking join to a thread pool — never block the event loop
                await asyncio.get_running_loop().run_in_executor(
                    None, self._recv_thread.join, 1.0
                )
            logger.info("CAN Reader stopped.")

    def _spawn_recv_thread(self, event_loop: asyncio.AbstractEventLoop) -> threading.Thread:
        """Create and start a new receive thread."""
        t = threading.Thread(
            target=self._recv_loop,
            args=(event_loop,),
            daemon=True,
            name="can-reader-rx",
        )
        t.start()
        return t

    def _is_bus_stale(self) -> bool:
        """Return True when the current CAN bus has received no frame for too long."""
        silence_age = self._bus_silence_age_sec()
        return (
            self._stale_threshold_sec > 0.0
            and silence_age is not None
            and silence_age >= self._stale_threshold_sec
        )

    def _bus_silence_age_sec(self) -> float | None:
        freshness_start = self._last_recv_monotonic or self._bus_opened_monotonic
        if not freshness_start:
            return None
        return max(0.0, time.monotonic() - freshness_start)

    async def _close_recv_thread(self) -> None:
        """Close the current bus and wait briefly for its blocked recv() to return."""
        try:
            self._bus.shutdown()
        except Exception:
            logger.debug("CAN bus shutdown during reconnect failed", exc_info=True)

        if self._recv_thread and self._recv_thread.is_alive():
            await asyncio.get_running_loop().run_in_executor(
                None, self._recv_thread.join, 1.0
            )
        if self._recv_thread and self._recv_thread.is_alive():
            self._fatal_error = "recv_thread_stuck"
            logger.critical("CAN recv thread did not exit after bus shutdown")
            self.stop()

    async def request_reconnect(self) -> bool:
        """Start one reconnect loop; return False when recovery is already in progress."""
        if not self._running or self._reconnecting:
            return False

        self._reconnecting = True
        await self._close_recv_thread()
        if not self._running:
            self._reconnecting = False
            return False

        self._reconnect_task = asyncio.create_task(
            self._reconnect(), name="can-reader-reconnect"
        )
        return True

    def _recv_loop(self, event_loop: asyncio.AbstractEventLoop) -> None:
        """Run in a dedicated OS thread: a tight recv() loop that posts frames via call_soon_threadsafe.

        There is no asyncio overhead for each recv() call — this completely removes
        the scheduling cost of run_in_executor (~5-10 µs/frame).
        """
        logger.debug("CAN recv thread started (tid=%d)", threading.get_ident())
        bus = self._bus  # local snapshot — avoids race with _reconnect() reassigning self._bus
        while self._running:
            try:
                msg: can.Message | None = bus.recv(timeout=0.2)
                if msg is None:
                    continue
                self._received_count += 1
                self._last_frame_timestamp = msg.timestamp if msg.timestamp else time.time()
                self._last_recv_monotonic = time.monotonic()
                self._last_error = None
                # Opening a socket is not enough to prove recovery. Reset the
                # staged backoff only after the replacement bus delivers data.
                self._reconnect_attempt = 0
                # Copy data before posting — some backends reuse internal buffers
                msg_copy = can.Message(
                    arbitration_id=msg.arbitration_id,
                    data=bytes(msg.data),
                    timestamp=msg.timestamp,
                    is_extended_id=msg.is_extended_id,
                    is_fd=msg.is_fd,
                )
                # Filter/decode before crossing into the event loop. Posting every
                # received frame can build an unbounded call_soon_threadsafe backlog
                # under sustained real CAN load, even when the asyncio.Queue is bounded.
                arrival = time.monotonic()
                frame = self._prepare_frame(msg_copy, arrival)
                if frame is not None:
                    self._submit_frame(event_loop, frame)
            except can.CanError as exc:
                self._error_count += 1
                self._last_error = str(exc)
                logger.error("CAN bus error #%d: %s — recv thread exiting", self._error_count, exc)
                break  # watchdog detects thread death and initiates reconnect
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("Unexpected error in CAN recv thread: %s", exc)
                break
        logger.debug("CAN recv thread exited")

    def _enqueue_sync(self, msg: can.Message, arrival: float = 0.0) -> None:
        """Compatibility helper used by tests: prepare then enqueue a CAN message."""
        frame = self._prepare_frame(msg, arrival)
        if frame is not None:
            self._enqueue_frame_sync(frame)

    def _submit_frame(
        self,
        event_loop: asyncio.AbstractEventLoop,
        frame: DecodedFrame,
    ) -> None:
        """Coalesce thread-side frames and schedule at most one loop callback."""
        if self._event_loop is None:
            self._event_loop = event_loop
        should_schedule = False
        with self._pending_lock:
            msg_id = frame.raw.msg_id
            pending = self._pending_frames.get(msg_id)
            if pending is None:
                self._pending_frames[msg_id] = frame
            else:
                pending.raw = frame.raw
                pending.msg_name = frame.msg_name
                pending.signals.update(frame.signals)
                self._coalesced_count += 1

            if not self._drain_scheduled:
                self._drain_scheduled = True
                self._ingress_callbacks_scheduled += 1
                should_schedule = True

        if not should_schedule:
            return

        try:
            event_loop.call_soon_threadsafe(self._drain_pending_frames)
        except RuntimeError:
            # The event loop may already be closing during process shutdown.
            with self._pending_lock:
                self._drain_scheduled = False
                self._pending_frames.clear()

    def _drain_pending_frames(self) -> None:
        """Move one coalesced ingress snapshot onto the bounded asyncio queue."""
        with self._pending_lock:
            frames = list(self._pending_frames.values())
            self._pending_frames.clear()

        for frame in frames:
            self._enqueue_frame_sync(frame)

        schedule_next = False
        with self._pending_lock:
            if self._pending_frames:
                self._ingress_callbacks_scheduled += 1
                schedule_next = True
            else:
                self._drain_scheduled = False

        if schedule_next and self._event_loop is not None:
            try:
                self._event_loop.call_soon(self._drain_pending_frames)
            except RuntimeError:
                with self._pending_lock:
                    self._drain_scheduled = False
                    self._pending_frames.clear()

    def _prepare_frame(self, msg: can.Message, arrival: float = 0.0) -> DecodedFrame | None:
        """Filter, rate-gate, decode, and deduplicate a raw CAN message.

        This runs in the recv thread before scheduling work onto the asyncio loop.
        Keeping drops on this side prevents stale event-loop callbacks from piling up
        during multi-day high-rate CAN runs.
        """
        if self._filter_ids and msg.arbitration_id not in self._filter_ids:
            return None

        now_t = arrival if arrival else time.monotonic()

        if self._min_interval > 0.0:
            last_t = self._last_enqueue.get(msg.arbitration_id, 0.0)
            if (now_t - last_t) < self._min_interval:
                self._rate_limited_count += 1
                return None
            self._last_enqueue[msg.arbitration_id] = now_t

        # Skip message if raw data bytes are unchanged AND no signal is overdue for refresh
        last_data = self._last_msg_data.get(msg.arbitration_id)
        if (
            last_data is not None
            and last_data == msg.data
            and not self._is_stale_message(msg.arbitration_id, now_t)
        ):
            return None
        self._last_msg_data[msg.arbitration_id] = msg.data

        frame = self._decode(msg)
        if not frame.signals:
            logger.debug("msg_id=%#x has no signals in DB", msg.arbitration_id)
            return None

        # Keep signals whose value changed OR that are overdue for a refresh (low-freq priority)
        changed: dict[str, float] = {}
        for sig_name, value in frame.signals.items():
            is_changed = self._last_signal_values.get(sig_name) != value
            is_stale = (
                self._priority_sec > 0.0
                and (now_t - self._signal_last_enqueue_time.get(sig_name, 0.0)) >= self._priority_sec
            )
            if is_changed or is_stale:
                changed[sig_name] = value
                self._last_signal_values[sig_name] = value
                self._signal_last_enqueue_time[sig_name] = now_t
        if not changed:
            return None
        frame.signals = changed

        return frame

    def _enqueue_frame_sync(self, frame: DecodedFrame) -> None:
        """Put an already prepared frame onto the asyncio queue from the event-loop thread."""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            if self._policy == "drop_oldest":
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(frame)
                    logger.debug(
                        "RX queue full — dropped oldest frame (msg_id=%#x)",
                        frame.raw.msg_id,
                    )
                    return  # frame successfully placed — NOT counted as dropped
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # specific exceptions
                    pass
            # Truly dropped — now increment counter
            self._dropped_count += 1
            self._drop_warn_count += 1
            now_w = time.monotonic()
            if (now_w - self._drop_warn_last_log) >= _DROP_WARN_INTERVAL_SEC:
                logger.warning(
                    "RX queue full — %d frame(s) dropped in last %.0fs "
                    "(last msg_id=%#x, queue=%d/%d, rate_limited=%d total)",
                    self._drop_warn_count,
                    (
                        now_w - self._drop_warn_last_log
                        if self._drop_warn_last_log
                        else _DROP_WARN_INTERVAL_SEC
                    ),
                    frame.raw.msg_id,
                    self._queue.qsize(),
                    self._queue.maxsize,
                    self._rate_limited_count,
                )
                self._drop_warn_count = 0
                self._drop_warn_last_log = now_w


    def stop(self) -> None:
        """Signal the read loop to stop cleanly."""
        self._running = False
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        with self._pending_lock:
            self._pending_frames.clear()
            self._drain_scheduled = False

    def _is_stale_message(self, msg_id: int, now: float) -> bool:
        """True if any signal in the message has not been enqueued in more than _priority_sec seconds.

        Used to bypass message-level dedup for messages whose data does not change but
        contain "rarely changing" signals that still need periodic refresh.
        """
        if self._priority_sec <= 0.0:
            return False
        msg_def = self._db.messages.get(msg_id)
        if msg_def is None:
            return False
        for sig_name in msg_def.signals:
            if (now - self._signal_last_enqueue_time.get(sig_name, 0.0)) >= self._priority_sec:
                return True
        return False

    # ── Decoding ─────────────────────────────────────────────────────────────

    def _decode(self, msg: can.Message) -> DecodedFrame:
        raw = RawCANFrame(
            timestamp=msg.timestamp if msg.timestamp else time.time(),
            bus=getattr(self._bus, "channel", "unknown"),
            msg_id=msg.arbitration_id,
            is_extended=msg.is_extended_id,
            is_fd=msg.is_fd,
            data=bytes(msg.data),
        )
        signals = self._db.decode_frame(msg.arbitration_id, bytes(msg.data))
        msg_def = self._db.messages.get(msg.arbitration_id)
        return DecodedFrame(
            raw=raw,
            signals=signals,
            msg_name=msg_def.name if msg_def else "",
        )

    # ── Reconnection ─────────────────────────────────────────────────────────

    async def _reconnect(self, max_retries: int | None = None) -> None:
        """Try to reopen the CAN bus with staged backoff up to one retry per hour."""
        retries = max_retries if max_retries is not None else self._max_retries
        bounded_retries = max_retries is not None
        attempts_this_call = 0
        try:
            while self._running:
                attempts_this_call += 1
                self._reconnect_attempt += 1
                attempt = self._reconnect_attempt
                delay = self._reconnect_delay(attempt, self._max_retries)
                logger.info("Reconnect attempt %d in %ds...", attempt, delay)
                await asyncio.sleep(delay)
                if not self._running:  # honour stop() during reconnect backoff
                    return
                try:
                    self._bus.shutdown()
                    if self._bus_factory is None:
                        logger.warning("No bus_factory — cannot re-open bus; stopping reader")
                        self._fatal_error = "no_bus_factory"
                        self.stop()
                        return

                    self._bus = self._bus_factory()
                    if self._on_bus_reconnected is not None:
                        await self._on_bus_reconnected(self._bus)
                    # Force a complete first snapshot from the replacement bus;
                    # otherwise unchanged data from before the outage could be
                    # deduplicated forever when priority refresh is disabled.
                    self._last_enqueue.clear()
                    self._last_msg_data.clear()
                    self._last_signal_values.clear()
                    self._signal_last_enqueue_time.clear()
                    # Start the freshness clock at reopen time so a bus that
                    # remains silent advances through the retry schedule. The
                    # attempt counter resets only when _recv_loop gets a frame.
                    self._last_recv_monotonic = 0.0
                    self._bus_opened_monotonic = time.monotonic()
                    if self._event_loop is not None:
                        self._recv_thread = self._spawn_recv_thread(self._event_loop)
                    logger.info("CAN bus re-opened: %s", self._bus)
                    return
                except can.CanError as exc:
                    self._last_error = str(exc)
                    logger.warning("Reconnect attempt %d failed: %s", attempt, exc)
                except Exception as exc:
                    self._last_error = str(exc)
                    logger.warning("Reconnect attempt %d failed unexpectedly: %s", attempt, exc)
                if bounded_retries and attempts_this_call >= retries:
                    logger.critical(
                        "CAN bus reconnect failed after %d attempts — supervisor must intervene",
                        retries,
                    )
                    self._fatal_error = "reconnect_failed"
                    self.stop()
                    return
        finally:
            self._reconnecting = False

    @staticmethod
    def _reconnect_delay(attempt: int, initial_retries: int) -> int:
        """Return reconnect delay: fast retries, then 10 attempts per longer interval."""
        if attempt <= initial_retries:
            return min(2 ** (attempt - 1), 30)

        retry_index = attempt - initial_retries - 1
        interval_index, _ = divmod(retry_index, 10)
        return min(30 * (2 ** interval_index), 3600)

    def get_metrics(self) -> dict:
        """Return internal reader metrics (dropped frames, error count)."""
        return {
            "dropped_frames": int(self._dropped_count),
            "error_count": int(self._error_count),
            "rate_limited_frames": int(self._rate_limited_count),
            "received_frames": int(self._received_count),
            "coalesced_frames": int(self._coalesced_count),
            "ingress_callbacks_scheduled": int(self._ingress_callbacks_scheduled),
            "pending_frames": self._pending_frame_count(),
            "last_frame_timestamp": float(self._last_frame_timestamp),
            "last_recv_age_sec": self._bus_silence_age_sec(),
            "thread_alive": bool(self._recv_thread and self._recv_thread.is_alive()),
            "running": bool(self._running),
            "fatal_error": self._fatal_error,
            "last_error": self._last_error,
        }

    def get_runtime_state(self) -> dict:
        """Return the reader runtime state for watchdog/health checks."""
        return {
            "running": bool(self._running),
            "thread_alive": bool(self._recv_thread and self._recv_thread.is_alive()),
            "last_frame_timestamp": float(self._last_frame_timestamp),
            "last_recv_age_sec": self._bus_silence_age_sec(),
            "fatal_error": self._fatal_error,
            "last_error": self._last_error,
            "error_count": int(self._error_count),
            "reconnecting": bool(self._reconnecting),
            "reconnect_attempt": int(self._reconnect_attempt),
        }

    def _pending_frame_count(self) -> int:
        with self._pending_lock:
            return len(self._pending_frames)

    @property
    def has_fatal_error(self) -> bool:
        """True if the reader cannot recover by itself and needs a supervisor restart."""
        return self._fatal_error is not None

    # Runtime helpers
    def set_queue_policy(self, policy: str) -> None:
        """Update the queue policy at runtime."""
        self._policy = policy

    def set_queue(self, queue: asyncio.Queue) -> None:
        """Swap the reference to the output queue (the caller is responsible for data migration)."""
        self._queue = queue

    def apply_runtime_config(
        self,
        *,
        queue_policy: str,
        max_rate_hz: float,
        priority_sec: float,
        stale_threshold_sec: float,
    ) -> None:
        """Synchronize all reader settings that are safe to change live."""
        self._policy = queue_policy
        self._min_interval = (1.0 / max_rate_hz) if max_rate_hz > 0 else 0.0
        self._priority_sec = max(0.0, float(priority_sec))
        self._stale_threshold_sec = max(0.0, float(stale_threshold_sec))
