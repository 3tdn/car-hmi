"""Proxy MJPEG camera stream — use a single upstream connection and fan-out to many clients.

Context
-------
The camera (via CarPC) streams MJPEG from a fixed URL, for example::

    http://192.168.2.119:8080/stream

The upstream MJPG server allows only **ONE simultaneous connection** — there is
mutex behavior on the source server, so if two clients open direct connections
to the camera, the second one is rejected or hangs.

Meanwhile, the HMI must allow many end devices (each device is one user) to view
the stream at the same time. Solution: CarPC (backend) opens **exactly one**
connection to the camera, reads the MJPEG byte stream, and fan-outs (broadcasts)
that data to many clients over HTTP to CarPC — each client does not touch the
camera directly, so it does not violate the one-connection limit.

`CameraStreamProxy` is responsible for:
    * Opening/maintaining a single upstream connection (start when the first client arrives,
      stop when the last client leaves).
    * Automatically reconnecting when the upstream fails or drops.
    * Fan-out received byte chunks to all currently open subscribers.
    * Dropping stale chunks if one client is too slow to avoid blocking the broadcast
      for other clients.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_CONTENT_TYPE = "multipart/x-mixed-replace; boundary=frame"
_JPEG_EOI_MARKER = b"\xff\xd9"  # JPEG End-Of-Image marker — used for approximate frame counting


class CameraStreamProxy:
    """Manage one upstream MJPEG connection and fan it out to many subscribers."""

    def __init__(
        self,
        stream_url: str,
        *,
        reconnect_interval_sec: float = 3.0,
        connect_timeout_sec: float = 5.0,
        read_timeout_sec: float = 10.0,
        chunk_size: int = 4096,
        subscriber_queue_size: int = 64,
        startup_wait_sec: float = 5.0,
        fps_log_interval_sec: float = 5.0,
    ) -> None:
        self.stream_url = stream_url
        self._reconnect_interval_sec = reconnect_interval_sec
        self._connect_timeout_sec = connect_timeout_sec
        self._read_timeout_sec = read_timeout_sec
        self._chunk_size = chunk_size
        self._subscriber_queue_size = subscriber_queue_size
        self._startup_wait_sec = startup_wait_sec
        self._fps_log_interval_sec = fps_log_interval_sec

        self._subscribers: set[asyncio.Queue[bytes | None]] = set()
        self._lock = asyncio.Lock()
        self._upstream_task: asyncio.Task | None = None
        self._content_type = _DEFAULT_CONTENT_TYPE
        self._content_type_ready = asyncio.Event()
        self._connected = False
        self._last_error: str | None = None
        self._stopping = False

        # Approximate FPS based on the JPEG EOI marker (0xFFD9) — used only for logging/
        # monitoring, and does not affect the relay data sent to clients.
        self._fps: float = 0.0
        self._frame_count = 0
        self._fps_window_start = time.monotonic()
        self._prev_chunk_last_byte = b""

    # ── Status ─────────────────────────────────────────────────────────
    @property
    def viewer_count(self) -> int:
        return len(self._subscribers)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def content_type(self) -> str:
        return self._content_type

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def fps(self) -> float:
        """Approximate upstream FPS, recalculated every `fps_log_interval_sec` seconds."""
        return self._fps

    # ── Public route API ─────────────────────────────────────────────
    async def open_subscription(self) -> asyncio.Queue[bytes | None]:
        """Register a new client; start the upstream if needed and then return the queue.

        Wait up to `startup_wait_sec` to confirm the real Content-Type/boundary from the
        upstream (so the header returned to the client matches the actual data stream).
        If the upstream is already connected (from another subscriber), return immediately.
        """
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self._subscriber_queue_size)
        async with self._lock:
            self._subscribers.add(queue)
            if self._upstream_task is None or self._upstream_task.done():
                self._stopping = False
                self._content_type_ready.clear()
                self._upstream_task = asyncio.create_task(
                    self._run_upstream(), name="camera-upstream"
                )

        try:
            await asyncio.wait_for(self._content_type_ready.wait(), timeout=self._startup_wait_sec)
        except TimeoutError:
            logger.debug("Camera upstream content-type not confirmed within timeout; using default")
        return queue

    async def stream_queue(self, queue: asyncio.Queue[bytes | None]) -> AsyncIterator[bytes]:
        """Async generator that emits byte chunks to a subscribed client."""
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:  # sentinel — upstream has stopped
                    break
                yield chunk
        finally:
            await self._remove_subscriber(queue)

    async def aclose(self) -> None:
        """Stop the upstream task completely — called on application shutdown."""
        self._stopping = True
        task = self._upstream_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._upstream_task = None

    # ── Internal helpers ─────────────────────────────────────────────────────
    async def _remove_subscriber(self, queue: asyncio.Queue[bytes | None]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)
            if not self._subscribers and self._upstream_task is not None:
                self._stopping = True
                self._upstream_task.cancel()
                self._upstream_task = None

    def _broadcast(self, chunk: bytes) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(chunk)
            except asyncio.QueueFull:
                # Slow client: drop the oldest chunk to prioritize fresh data and avoid
                # blocking broadcasts for other clients.
                with contextlib.suppress(asyncio.QueueEmpty, asyncio.QueueFull):
                    q.get_nowait()
                    q.put_nowait(chunk)

    def _broadcast_end(self) -> None:
        for q in list(self._subscribers):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

    def _reset_fps_counter(self) -> None:
        self._frame_count = 0
        self._fps_window_start = time.monotonic()
        self._prev_chunk_last_byte = b""

    def _track_fps(self, chunk: bytes) -> None:
        """Estimate the number of JPEG frames in `chunk` and log FPS periodically.

        Counts JPEG End-Of-Image (0xFFD9) markers found in the chunk, including the
        case where the marker is split across two consecutive chunks (0xFF at the end
        of the previous chunk + 0xD9 at the start of the current chunk).
        """
        if not chunk:
            return

        frame_count_in_chunk = chunk.count(_JPEG_EOI_MARKER)
        if self._prev_chunk_last_byte == b"\xff" and chunk[:1] == b"\xd9":
            frame_count_in_chunk += 1
        self._frame_count += frame_count_in_chunk
        self._prev_chunk_last_byte = chunk[-1:]

        now = time.monotonic()
        elapsed = now - self._fps_window_start
        if elapsed >= self._fps_log_interval_sec:
            self._fps = self._frame_count / elapsed if elapsed > 0 else 0.0
            logger.info(
                "Camera upstream FPS: %.1f (%d frames / %.1fs, viewers=%d)",
                self._fps, self._frame_count, elapsed, self.viewer_count,
            )
            self._frame_count = 0
            self._fps_window_start = now

    async def _run_upstream(self) -> None:
        """Loop that maintains exactly one upstream connection and auto-reconnects on errors."""
        timeout = httpx.Timeout(
            connect=self._connect_timeout_sec,
            read=self._read_timeout_sec,
            write=self._read_timeout_sec,
            pool=self._read_timeout_sec,
        )
        try:
            while self._subscribers and not self._stopping:
                try:
                    async with (
                        httpx.AsyncClient(timeout=timeout) as client,
                        client.stream("GET", self.stream_url) as resp,
                    ):
                        resp.raise_for_status()
                        default_ct = _DEFAULT_CONTENT_TYPE
                        self._content_type = resp.headers.get("content-type", default_ct)
                        self._connected = True
                        self._last_error = None
                        self._content_type_ready.set()
                        self._reset_fps_counter()
                        logger.info("Camera upstream connected: %s", self.stream_url)
                        async for chunk in resp.aiter_bytes(self._chunk_size):
                            if not self._subscribers or self._stopping:
                                break
                            if chunk:
                                self._track_fps(chunk)
                                self._broadcast(chunk)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if self._stopping:
                        break
                    self._last_error = str(exc)
                    logger.warning("Camera upstream error (%s): %s", self.stream_url, exc)
                finally:
                    self._connected = False

                if not self._subscribers or self._stopping:
                    break
                await asyncio.sleep(self._reconnect_interval_sec)
        finally:
            self._connected = False
            self._fps = 0.0
            self._content_type_ready.set()  # release any subscribers waiting for startup
            self._broadcast_end()
