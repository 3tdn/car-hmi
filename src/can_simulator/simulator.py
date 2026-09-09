"""CAN Simulator — sends random CAN frames over the virtual bus from a DBC file.

``CANSimulator`` reads a ``.dbc`` file directly (via ``DatabaseLoader.load_dbc()``),
generates random values within ``[minimum, maximum]`` for each signal, encodes them,
and sends them on the virtual CAN bus.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

import time

import can

from src.can_io.parser import DatabaseLoader, _insert_bits, states_as_ints

logger = logging.getLogger(__name__)

_CARPC_SENDER_NAME = "CAR_PC"  # messages CarPC transmits are written by CANWriter, not simulated here


@dataclass
class _SigDef:
    """Definition of a signal to simulate."""

    name: str
    start_bit: int
    length: int
    is_signed: bool
    big_endian: bool
    factor: float
    offset: float
    minimum: float
    maximum: float
    states: list[int] = field(default_factory=list)


@dataclass
class _MsgDef:
    """Definition of a message to simulate."""

    msg_id: int
    name: str
    dlc: int
    signals: list[_SigDef] = field(default_factory=list)


class CANSimulator:
    """Simulation engine that reads message/signal definitions from a DBC file
    (via ``DatabaseLoader.load_dbc()``) and generates uniformly random values in
    ``[minimum, maximum]`` for each signal.

    Messages sent by CarPC (``senders`` includes ``CAR_PC``) are skipped — those are
    written by ``CANWriter`` instead; the simulator only emits the ECU→CarPC direction.
    """

    def __init__(
        self,
        bus: can.BusABC,
        can_db_file: str | Path,
        cycle_ms: int = 50,
        repeat: bool = True,
        random_mode: bool = False,
    ) -> None:
        """
        Parameters:
            bus:          An opened ``can.BusABC`` instance for transmission.
            can_db_file:  Path to the ``.dbc`` file.
            cycle_ms:     Transmit cycle in milliseconds — sourced from ``cfg.simulator.default_cycle_ms``.
            repeat:       If ``True``, run continuously until ``stop()``.
            random_mode:  If ``False``, each transmit cycle uses the raw value (or state) incremented by one.
                          If ``True``, uses ``random.uniform()`` as usual.
        """
        self._bus = bus
        self._cycle_ms = cycle_ms
        self._repeat = repeat
        self._running = False
        self._random_mode = random_mode
        self._signal_values: dict[tuple[int, str], int] = {}
        db_loader = DatabaseLoader()
        db_loader.load_dbc(Path(can_db_file))
        self._messages = self._build_sim_messages(db_loader)
        logger.info(
            "CANSimulator initialized: %d messages, %d signals total from '%s'",
            len(self._messages),
            sum(len(m.signals) for m in self._messages),
            can_db_file,
        )

    # ── Build simulated messages from DatabaseLoader ────────────────────────

    @staticmethod
    def _build_sim_messages(db_loader: DatabaseLoader) -> list[_MsgDef]:
        """Convert loaded ParsedMessage/ParsedSignal into simulator message defs.

        Skips messages transmitted by CarPC — those are the CANWriter's responsibility.
        """
        result: list[_MsgDef] = []
        for msg in db_loader.messages.values():
            if _CARPC_SENDER_NAME in msg.senders:
                continue
            sigs = [
                _SigDef(
                    name=sig.name,
                    start_bit=sig.start_bit,
                    length=sig.length,
                    is_signed=sig.is_signed,
                    big_endian=sig.byte_order == "big_endian",
                    factor=sig.factor,
                    offset=sig.offset,
                    minimum=sig.minimum if sig.minimum is not None else 0.0,
                    maximum=sig.maximum if sig.maximum is not None else 0.0,
                    states=states_as_ints(sig.states),
                )
                for sig in msg.signals.values()
            ]
            if sigs:
                result.append(_MsgDef(msg_id=msg.msg_id, name=msg.name, dlc=msg.dlc, signals=sigs))
        return result

    # ── Transmit loop ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start transmitting random signal values on the ``cycle_ms`` interval."""
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
        """One cycle: generate values, encode them, and send every message.

        Measure the actual send time, then sleep for the remaining portion of the cycle
        to avoid drift (actual_period = send_time + sleep instead of cycle_ms).
        """
        t_start = time.monotonic()
        for msg_def in self._messages:
            frame_data = bytearray(msg_def.dlc)
            for sig in msg_def.signals:
                # compute valid raw range
                if sig.is_signed:
                    r_min = -(1 << (sig.length - 1))
                    r_max = (1 << (sig.length - 1)) - 1
                else:
                    r_min = 0
                    r_max = (1 << sig.length) - 1

                if not self._random_mode:
                    key = (msg_def.msg_id, sig.name)
                    if sig.states:
                        # cycle through defined states
                        prev_idx = self._signal_values.get(key, -1)
                        next_idx = prev_idx + 1
                        if next_idx >= len(sig.states):
                            next_idx = 0
                        self._signal_values[key] = next_idx
                        raw_val = sig.states[next_idx]
                    elif sig.length == 1:
                        # 1-bit flags (e.g. COM_Status_*) represent ECU/status health —
                        # hold them at "set" instead of toggling every tick
                        raw_val = r_max
                        self._signal_values[key] = raw_val
                    else:
                        # increment raw value by 1 each tick, wrapping around
                        prev = self._signal_values.get(key, r_min)
                        raw_val = prev + 1
                        if raw_val > r_max:
                            raw_val = r_min
                        self._signal_values[key] = raw_val
                else:
                    physical = random.uniform(sig.minimum, sig.maximum)  # noqa: S311
                    raw_val = round((physical - sig.offset) / sig.factor)
                # Clamp raw to the valid bit range
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
        """Stop the transmit loop after the current cycle."""
        self._running = False
