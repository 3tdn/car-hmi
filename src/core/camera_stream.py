"""Proxy MJPEG camera stream — dùng 1 kết nối upstream, fan-out cho nhiều client.

Bối cảnh
--------
Camera (qua CarPC) phát MJPEG stream tại một URL cố định, ví dụ::

    http://192.168.2.119:8080/stream

MJPG server phía nguồn chỉ cho phép **DUY NHẤT 1 kết nối đồng thời** — có
mutex ở phía server nguồn nên nếu 2 client cùng mở kết nối trực tiếp tới
camera, kết nối thứ 2 sẽ bị từ chối/treo.

Trong khi đó, HMI cần cho phép nhiều thiết bị đầu cuối (mỗi thiết bị là 1
user) cùng xem stream cùng lúc. Giải pháp: CarPC (backend) mở **đúng 1**
kết nối tới camera, đọc byte-stream MJPEG và fan-out (broadcast) cho nhiều
client tải dữ liệu qua HTTP tới CarPC — mỗi client không đụng tới camera
trực tiếp nên không vi phạm giới hạn 1-connection của camera.

`CameraStreamProxy` chịu trách nhiệm:
    * Mở/giữ 1 kết nối upstream duy nhất (khởi động khi có client đầu tiên,
      dừng khi client cuối cùng rời đi).
    * Tự động reconnect khi upstream lỗi/rớt kết nối.
    * Fan-out các chunk byte nhận được cho tất cả subscriber đang mở.
    * Bỏ (drop) chunk cũ nếu 1 client quá chậm để không làm nghẽn broadcast
      cho các client khác.
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
_JPEG_EOI_MARKER = b"\xff\xd9"  # JPEG End-Of-Image marker — dùng để đếm frame gần đúng


class CameraStreamProxy:
    """Quản lý 1 kết nối upstream MJPEG và fan-out cho nhiều subscriber."""

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

        # Đếm FPS gần đúng dựa trên marker JPEG EOI (0xFFD9) — chỉ dùng để log/giám
        # sát, không ảnh hưởng tới dữ liệu relay cho client.
        self._fps: float = 0.0
        self._frame_count = 0
        self._fps_window_start = time.monotonic()
        self._prev_chunk_last_byte = b""

    # ── Trạng thái ─────────────────────────────────────────────────────────
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
        """FPS gần đúng của upstream, tính lại mỗi `fps_log_interval_sec` giây."""
        return self._fps

    # ── API công khai cho route ─────────────────────────────────────────────
    async def open_subscription(self) -> asyncio.Queue[bytes | None]:
        """Đăng ký 1 client mới; khởi động upstream nếu cần rồi trả về queue.

        Chờ tối đa `startup_wait_sec` để xác định Content-Type/boundary thật
        từ upstream (đảm bảo header trả cho client khớp với dữ liệu thực tế).
        Nếu upstream đã kết nối sẵn (có subscriber khác), trả về ngay.
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
        """Async generator phát các chunk byte cho 1 client đã đăng ký."""
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:  # sentinel — upstream đã dừng
                    break
                yield chunk
        finally:
            await self._remove_subscriber(queue)

    async def aclose(self) -> None:
        """Dừng hẳn upstream task — gọi khi ứng dụng shutdown."""
        self._stopping = True
        task = self._upstream_task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
            self._upstream_task = None

    # ── Nội bộ ───────────────────────────────────────────────────────────────
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
                # Client chậm: bỏ chunk cũ nhất để ưu tiên dữ liệu tươi, tránh
                # nghẽn broadcast cho các client khác.
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
        """Đếm số frame JPEG gần đúng trong `chunk` và log FPS định kỳ.

        Đếm số marker JPEG End-Of-Image (0xFFD9) xuất hiện trong chunk, có xử lý
        trường hợp marker bị chia đôi giữa 2 chunk liên tiếp (byte 0xFF ở cuối
        chunk trước + byte 0xD9 ở đầu chunk này).
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
        """Vòng lặp giữ đúng 1 kết nối upstream, tự reconnect khi lỗi."""
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
            self._content_type_ready.set()  # giải phóng mọi subscriber đang chờ startup
            self._broadcast_end()
