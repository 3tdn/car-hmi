"""Encode signal values into CAN frames and send them on the bus."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import can

from src.can_io.parser import DatabaseLoader

if TYPE_CHECKING:
    from src.core.config import WriterConfig
    from src.core.signal_store import SignalStore

logger = logging.getLogger(__name__)


class CANWriter:
    """Encode signal values into CAN frames and send them on the bus.

    All write operations are serialized through ``asyncio.Lock`` to avoid
    concurrent frame transmission from multiple async callers.

    If ``signal_store`` is provided, ``send_signal`` will:
    1. Read the current values of signals in the same message (read-modify-write)
       so other signals in the same CAN frame are not zeroed out.
    2. Update SignalStore directly after a successful send, because SocketCAN
       does not loop back frames from the same socket by default (recv_own_msgs=False).

    When ``periodic_mode=True`` (from WriterConfig), each ``send_signals_batch``
    sends immediately, then continues retransmitting at ``periodic_time_step`` ms intervals
    for a total duration of ``periodic_duration`` ms. Rate limiting and burst are ignored.
    """

    def __init__(
        self,
        bus: can.BusABC,
        db: DatabaseLoader,
        signal_store: "SignalStore | None" = None,
        writer_config: "WriterConfig | None" = None,
    ) -> None:
        """
        Args:
            bus:           Open ``can.Bus`` object used for transmission.
            db:            ``DatabaseLoader`` used to encode signals.
            signal_store:  Reference to SignalStore for read-modify-write and
                           dashboard updates after sending (optional).
            writer_config: Writer configuration (periodic mode, rate limit, ...).
        """
        self._bus = bus
        self._db = db
        self._store = signal_store
        self._lock = asyncio.Lock()
        self._sent_count = 0

        # Periodic mode config
        if writer_config is not None:
            self._periodic_mode = writer_config.periodic_mode
            self._periodic_time_step_ms = writer_config.periodic_time_step
            self._periodic_duration_ms = writer_config.periodic_duration
        else:
            self._periodic_mode = False
            self._periodic_time_step_ms = 20
            self._periodic_duration_ms = 10000

        # Periodic task management: msg_id → asyncio.Task
        self._periodic_tasks: dict[int, asyncio.Task] = {}

    async def send_signal(self, name: str, value: float) -> None:
        """Encode a single signal and transmit the corresponding CAN frame.

        Delegates to ``send_signals_batch`` so both paths share the same
        read-modify-write logic (preserving the other signals in the same message).

        Args:
            name:  Signal name as defined in the DBC/CANdb database.
            value: Physical value (engineering units).

        Raises:
            ValueError: if signal ``name`` is not found in the DB.
            can.CanError: if ``bus.send()`` fails.
        """
        await self.send_signals_batch({name: value})

    async def send_signals_batch(self, signals: dict[str, float]) -> dict[str, float]:
        """Group multiple signals by message ID and send exactly one frame per message.

        For each CAN message referenced in ``signals``:
        - Read the current values of all remaining signals in the message from
          SignalStore (read-modify-write) so they are not zeroed out.
        - Override them with the new values in ``signals``.
        - Encode and send a single CAN frame for that message.

        Args:
            signals: dict {signal_name → physical_value} for all signals to write.

        Returns:
            dict {signal_name → value} for the signals that were sent successfully.

        Raises:
            ValueError: if a signal is not found in this channel's DB.
        """
        # ── Step 1: group by message ────────────────────────────────────────────
        from src.can_io.parser import ParsedMessage  # avoid top-level circular import

        msg_groups: dict[int, dict[str, float]] = {}
        msg_defs: dict[int, ParsedMessage] = {}

        for sig_name, value in signals.items():
            msg_def = self._db.get_message_for_signal(sig_name)
            if msg_def is None:
                raise ValueError(
                    f"Signal '{sig_name}' not found in CAN database — cannot encode"
                )
            if msg_def.msg_id not in msg_groups:
                msg_groups[msg_def.msg_id] = {}
                msg_defs[msg_def.msg_id] = msg_def
            msg_groups[msg_def.msg_id][sig_name] = value

        # ── Step 2: send a single frame for each message ───────────────────────
        sent: dict[str, float] = {}
        ts = time.time()

        for msg_id, sig_values in msg_groups.items():
            msg_def = msg_defs[msg_id]
            await self._send_frame(msg_id, msg_def, sig_values, ts)

            if self._periodic_mode:
                # Cancel the previous periodic task (if any) for this msg_id
                old_task = self._periodic_tasks.pop(msg_id, None)
                if old_task is not None and not old_task.done():
                    old_task.cancel()
                # Start a new periodic task
                task = asyncio.create_task(
                    self._periodic_sender(msg_id, msg_def, dict(sig_values)),
                    name=f"periodic-writer-{msg_id:#x}",
                )
                self._periodic_tasks[msg_id] = task

            sent.update(sig_values)

        # ── Step 3: update SignalStore (fire-and-forget) ──────────────────────
        # Keep this off the await chain so the HTTP response can return right after the CAN frame
        # has been sent, without being blocked by WebSocket broadcasting.
        if self._store is not None and sent:
            asyncio.create_task(self._store.bulk_update(sent, timestamp=ts))

        return sent

    async def _send_frame(
        self,
        msg_id: int,
        msg_def: object,
        sig_values: dict[str, float],
        ts: float,
    ) -> None:
        """Perform read-modify-write and then send one CAN frame for ``msg_id``."""
        signals_to_encode: dict[str, float] = {}
        if self._store is not None:
            for sig_name in msg_def.signals:
                if sig_name in sig_values:
                    continue
                sv = await self._store.get(sig_name)
                if sv is not None:
                    signals_to_encode[sig_name] = sv.value

        signals_to_encode.update(sig_values)

        msg = self._db.encode_message(msg_id, signals_to_encode)
        if msg is None:
            raise ValueError(f"Failed to encode message {msg_id:#x}")
        msg.timestamp = ts

        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._bus.send, msg)
            except Exception as exc:
                raise can.CanError(
                    f"Failed to send CAN frame for msg_id={msg_id:#x}: {exc}"
                ) from exc
            self._sent_count += 1
            logger.info(
                "CAN batch write [%d]: msg_id=%#x signals=%s data=%s",
                self._sent_count,
                msg_id,
                list(sig_values.keys()),
                msg.data.hex(),
            )

    async def _periodic_sender(
        self,
        msg_id: int,
        msg_def: object,
        sig_values: dict[str, float],
    ) -> None:
        """Repeatedly send a CAN frame every ``periodic_time_step`` ms for ``periodic_duration`` ms."""
        interval = self._periodic_time_step_ms / 1000.0
        deadline = time.monotonic() + self._periodic_duration_ms / 1000.0
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(interval)
                if time.monotonic() >= deadline:
                    break
                ts = time.time()
                await self._send_frame(msg_id, msg_def, sig_values, ts)
                if self._store is not None:
                    await self._store.bulk_update(sig_values, timestamp=ts)
        except asyncio.CancelledError:
            logger.debug("Periodic sender cancelled for msg_id=%#x", msg_id)
        finally:
            self._periodic_tasks.pop(msg_id, None)
            logger.debug(
                "Periodic sender stopped for msg_id=%#x after %.1f ms",
                msg_id,
                self._periodic_duration_ms,
            )

    async def send_message(self, msg_id: int, signals: dict[str, float]) -> None:
        """Encode an entire message by ID and send it.

        Args:
            msg_id:  CAN arbitration ID.
            signals: Dict {signal_name: physical_value} for all signals to encode.

        Raises:
            ValueError: if ``msg_id`` is not found in the DB.
            can.CanError: if sending fails.
        """
        msg = self._db.encode_message(msg_id, signals)
        if msg is None:
            raise ValueError(f"Message ID {msg_id:#x} not found in CAN database — cannot encode")
        msg.timestamp = time.time()
        async with self._lock:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(None, self._bus.send, msg)
            except Exception as exc:
                raise can.CanError(
                    f"Failed to send CAN message {msg_id:#x}: {exc}"
                ) from exc
            self._sent_count += 1
            logger.debug(
                "CAN write msg [%d]: msg_id=%#x signals=%s",
                self._sent_count,
                msg_id,
                list(signals.keys()),
            )


class CANWriterRouter:
    """Route signal write requests to the correct CANWriter by channel.

    Builds O(1) lookup maps: signal_name → CANWriter, msg_id → CANWriter
    to avoid linear searches during writes.
    """

    def __init__(self) -> None:
        self._signal_to_writer: dict[str, CANWriter] = {}
        self._msgid_to_writer: dict[int, CANWriter] = {}
        self._writers: list[CANWriter] = []

    def register(self, db: DatabaseLoader, writer: CANWriter) -> None:
        """Register a CANWriter together with its corresponding DatabaseLoader."""
        self._writers.append(writer)
        for sig_name in db.signals:
            if sig_name in self._signal_to_writer:
                logger.debug(
                    "CANWriterRouter: signal '%s' already registered "
                    "on another channel — keeping first registration",
                    sig_name,
                )
                continue
            self._signal_to_writer[sig_name] = writer
        for msg_id in db.messages:
            if msg_id in self._msgid_to_writer:
                logger.debug(
                    "CANWriterRouter: msg_id %#x already registered "
                    "on another channel — keeping first registration",
                    msg_id,
                )
                continue
            self._msgid_to_writer[msg_id] = writer

    async def send_signal(self, name: str, value: float) -> None:
        """Route and send a signal through the correct CAN channel."""
        writer = self._signal_to_writer.get(name)
        if writer is None:
            raise ValueError(
                f"Signal '{name}' not found in any CAN channel — cannot encode"
            )
        await writer.send_signal(name, value)

    async def send_message(self, msg_id: int, signals: dict[str, float]) -> None:
        """Route and send a message through the correct CAN channel."""
        writer = self._msgid_to_writer.get(msg_id)
        if writer is None:
            raise ValueError(
                f"Message ID {msg_id:#x} not found in any CAN channel — cannot encode"
            )
        await writer.send_message(msg_id, signals)

    async def send_signals_batch(
        self, signals: dict[str, float]
    ) -> tuple[dict[str, float], list[dict]]:
        """Group a signal batch by channel and send it, with one frame per CAN message.

        Signals not found on any channel are collected into an error list
        instead of raising an exception, so valid signals can still be sent.

        Args:
            signals: dict {canonical_signal_name → physical_value}

        Returns:
            (sent, errors)
            - sent:   dict {signal_name → value} for signals sent successfully
            - errors: list[{"signal_name": ..., "error": ...}] for failed signals
        """
        # ── Classify signal → writer ───────────────────────────────────────────
        writer_groups: dict[int, tuple[CANWriter, dict[str, float]]] = {}
        errors: list[dict] = []

        for sig_name, value in signals.items():
            writer = self._signal_to_writer.get(sig_name)
            if writer is None:
                errors.append(
                    {
                        "signal_name": sig_name,
                        "error": f"Signal '{sig_name}' not found in any CAN channel",
                    }
                )
                continue
            wid = id(writer)
            if wid not in writer_groups:
                writer_groups[wid] = (writer, {})
            writer_groups[wid][1][sig_name] = value

        # ── Send batches for each channel ───────────────────────────────────────
        sent: dict[str, float] = {}
        for writer, sig_map in writer_groups.values():
            try:
                result = await writer.send_signals_batch(sig_map)
                sent.update(result)
            except ValueError as exc:
                # Put every signal from this channel into errors
                for sig_name in sig_map:
                    errors.append({"signal_name": sig_name, "error": str(exc), "kind": "value"})
            except can.CanError as exc:
                for sig_name in sig_map:
                    errors.append({"signal_name": sig_name, "error": str(exc), "kind": "transport"})
            except Exception as exc:
                for sig_name in sig_map:
                    errors.append({"signal_name": sig_name, "error": str(exc), "kind": "unknown"})

        return sent, errors
