"""Regression test for bounded CAN-to-asyncio ingress under sustained changing traffic."""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import uuid
from types import SimpleNamespace

import can
import pytest

from src.can_io.reader import CANReader


@pytest.mark.skipif(
    os.getenv("RUN_CAN_BACKLOG_TEST") != "1",
    reason="set RUN_CAN_BACKLOG_TEST=1 to run the virtual-CAN backlog test",
)
@pytest.mark.asyncio
async def test_virtual_can_changing_frames_keep_one_pending_loop_callback():
    """Block the event loop while changing frames arrive and verify ingress remains bounded."""
    frame_count = 10_000
    decoded_all_frames = threading.Event()
    channel = f"callback-backlog-{uuid.uuid4()}"
    rx_bus = can.Bus(interface="virtual", channel=channel)
    tx_bus = can.Bus(interface="virtual", channel=channel)

    class CountingDB:
        def __init__(self) -> None:
            self.messages = {
                0x123: SimpleNamespace(
                    name="ChangingMessage",
                    signals={"ChangingSignal": None},
                )
            }
            self.decoded = 0

        def decode_frame(self, _msg_id: int, data: bytes) -> dict[str, float]:
            self.decoded += 1
            if self.decoded == frame_count:
                decoded_all_frames.set()
            return {"ChangingSignal": float(data[0])}

    db = CountingDB()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    reader = CANReader(
        bus=rx_bus,
        db=db,
        queue=queue,
        max_rate_hz=0,
        stale_threshold_sec=0,
    )
    reader_task = asyncio.create_task(reader.start())
    await asyncio.sleep(0.05)

    producer_errors: list[Exception] = []

    def _produce() -> None:
        try:
            for index in range(frame_count):
                tx_bus.send(
                    can.Message(
                        arbitration_id=0x123,
                        data=bytes([index % 2]),
                        is_extended_id=False,
                    )
                )
        except Exception as exc:
            producer_errors.append(exc)

    producer = threading.Thread(target=_produce, name="virtual-can-backlog-producer")
    producer.start()

    try:
        # This intentionally blocks the asyncio loop. The CAN recv thread must
        # coalesce all arriving updates without adding one callback per frame.
        assert decoded_all_frames.wait(timeout=10.0)
        producer.join(timeout=2.0)
        assert not producer.is_alive()
        assert producer_errors == []

        metrics = reader.get_metrics()
        assert metrics["received_frames"] == frame_count
        assert metrics["ingress_callbacks_scheduled"] == 1
        assert metrics["coalesced_frames"] == frame_count - 1
        assert metrics["pending_frames"] == 1
        assert queue.qsize() == 0

        await asyncio.sleep(0)
        assert queue.qsize() == 1
        assert queue.get_nowait().signals == {"ChangingSignal": 1.0}
    finally:
        reader.stop()
        reader_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reader_task
        rx_bus.shutdown()
        tx_bus.shutdown()
