"""Quản lý kết nối WebSocket để push tín hiệu và cảnh báo thời gian thực.

Hỗ trợ:
- Topic-based subscription cũ (backward-compat): /ws/signals, /ws/alarms, /ws/all
- Per-signal subscription mới: /ws/subscribe — client gửi JSON command để chọn kênh
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class SubscriptionTopic(str, Enum):
    SIGNALS = "signals"
    ALARMS = "alarms"
    ALL = "all"


class _ClientSubscription:
    """State riêng cho 1 WS connection dùng giao thức subscribe mới."""

    __slots__ = ("signal_names", "subscribe_alarms", "subscribe_metrics", "once_channels", "min_interval_s")

    def __init__(self) -> None:
        self.signal_names: set[str] = set()  # rỗng = không nhận signal nào; "*" = tất cả
        self.subscribe_alarms: bool = False
        self.subscribe_metrics: bool = False
        # Channels đã yêu cầu mode "once" — sẽ bị gỡ sau khi gửi lần đầu
        self.once_channels: set[str] = set()

        # If > 0, minimum seconds between sends to this connection (client-requested)
        self.min_interval_s: float = 0.0

    def wants_signal(self, name: str) -> bool:
        if "*" in self.signal_names:
            return True
        return name in self.signal_names


class ConnectionManager:
    """Quản lý các kết nối WebSocket đang hoạt động và phát sóng fan-out."""

    def __init__(self) -> None:
        # Legacy topic-based connections
        self._connections: dict[WebSocket, set[SubscriptionTopic]] = {}
        # New per-signal subscription connections
        self._subscriptions: dict[WebSocket, _ClientSubscription] = {}
        # Track last send time per websocket for simple rate-limiting
        self._last_sent: dict[WebSocket, float] = {}
        self._lock = asyncio.Lock()

    # ── Legacy connect/disconnect ────────────────────────────────────────────

    async def connect(self, ws: WebSocket, topics: set[SubscriptionTopic] | None = None) -> None:
        await ws.accept()
        async with self._lock:
            self._connections[ws] = topics or {SubscriptionTopic.ALL}
        logger.debug("WS đã kết nối (legacy) — tổng số: %d", len(self._connections))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._connections.pop(ws, None)
            self._subscriptions.pop(ws, None)
        logger.debug("WS đã ngắt kết nối — tổng số: %d", len(self._connections) + len(self._subscriptions))

    # ── New subscribe-based connect ──────────────────────────────────────────

    async def connect_subscribe(self, ws: WebSocket) -> None:
        """Accept WS connection cho giao thức subscribe mới."""
        await ws.accept()
        async with self._lock:
            self._subscriptions[ws] = _ClientSubscription()
        logger.debug("WS subscribe đã kết nối — tổng số: %d", len(self._subscriptions))

    def _get_sub(self, ws: WebSocket) -> _ClientSubscription | None:
        return self._subscriptions.get(ws)

    async def process_subscribe_command(self, ws: WebSocket, data: dict) -> None:
        """Xử lý lệnh subscribe/unsubscribe từ client."""
        action = data.get("action", "subscribe")
        channels = data.get("channels", [])
        mode = data.get("mode", "continuous")
        # Optional per-connection rate limiting requested by client (ms)
        rate_ms = data.get("rate_ms")

        async with self._lock:
            sub = self._get_sub(ws)
            if sub is None:
                return

            # Apply rate limit if provided
            try:
                if rate_ms is not None:
                    # coerce to float seconds, clamp to >= 0
                    sub.min_interval_s = max(0.0, float(rate_ms) / 1000.0)
            except Exception:
                pass

            for ch in channels:
                ch_lower = ch.lower()
                if action == "subscribe":
                    if ch_lower == "alarms":
                        sub.subscribe_alarms = True
                    elif ch_lower == "metrics":
                        sub.subscribe_metrics = True
                    elif ch == "*":
                        sub.signal_names.add("*")
                    else:
                        sub.signal_names.add(ch)  # signal name — case-sensitive

                    if mode == "once":
                        sub.once_channels.add(ch)
                elif action == "unsubscribe":
                    if ch_lower == "alarms":
                        sub.subscribe_alarms = False
                    elif ch_lower == "metrics":
                        sub.subscribe_metrics = False
                    elif ch == "*":
                        sub.signal_names.discard("*")
                    else:
                        sub.signal_names.discard(ch)

        # Ack (outside lock to avoid holding lock during I/O)
        ack_payload = json.dumps({
            "type": "subscribe_ack",
            "action": action,
            "channels": channels,
            "mode": mode,
            "rate_ms": rate_ms,
        })
        try:
            await ws.send_text(ack_payload)
        except Exception:
            pass

    # ── Broadcast ────────────────────────────────────────────────────────────

    async def broadcast_signal(self, signal_name: str, value: float, timestamp: float) -> None:
        payload = json.dumps({
            "type": "signal",
            "signal": signal_name,
            "value": value,
            "timestamp": timestamp,
        })
        # Legacy broadcast
        await self._broadcast(payload, SubscriptionTopic.SIGNALS)
        # New per-signal broadcast
        await self._broadcast_to_subscribers(payload, signal_name=signal_name)

    async def broadcast_alarm(self, alarm: dict) -> None:
        payload = json.dumps({"type": "alarm", **alarm})
        await self._broadcast(payload, SubscriptionTopic.ALARMS)
        await self._broadcast_to_subscribers(payload, channel="alarms")

    async def broadcast_metrics(self, metrics: dict) -> None:
        """Push metrics snapshot tới subscribers đã đăng ký channel 'metrics'."""
        payload = json.dumps({"type": "metrics", **metrics})
        await self._broadcast_to_subscribers(payload, channel="metrics")

    # ── Internal broadcast helpers ───────────────────────────────────────────

    async def _broadcast(self, text: str, topic: SubscriptionTopic) -> None:
        """Legacy broadcast cho /ws/signals, /ws/alarms, /ws/all."""
        stale: list[WebSocket] = []
        async with self._lock:
            snapshot = list(self._connections.items())

        async def _send(ws: WebSocket) -> WebSocket | None:
            try:
                await ws.send_text(text)
                return None
            except Exception:
                return ws

        tasks = []
        for ws, subs in snapshot:
            if topic in subs or SubscriptionTopic.ALL in subs:
                tasks.append(asyncio.create_task(_send(ws)))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if res is not None and not isinstance(res, Exception):
                    stale.append(res)

        for ws in stale:
            await self.disconnect(ws)

    async def _broadcast_to_subscribers(
        self,
        text: str,
        *,
        signal_name: str | None = None,
        channel: str | None = None,
    ) -> None:
        """Broadcast tới WS connections dùng giao thức subscribe mới."""
        stale: list[WebSocket] = []
        async with self._lock:
            snapshot = list(self._subscriptions.items())

        async def _send(ws: WebSocket) -> WebSocket | None:
            try:
                await ws.send_text(text)
                # record last sent time for simple rate limiting
                try:
                    self._last_sent[ws] = asyncio.get_event_loop().time()
                except Exception:
                    pass
                return None
            except Exception:
                return ws

        tasks = []
        once_remove: list[tuple[WebSocket, str]] = []
        now = asyncio.get_event_loop().time()
        for ws, sub in snapshot:
            should_send = False
            once_key: str | None = None
            if signal_name is not None:
                if sub.wants_signal(signal_name):
                    should_send = True
                    # check once
                    if signal_name in sub.once_channels:
                        once_key = signal_name
                    elif "*" in sub.once_channels:
                        once_key = "*"
            elif channel == "alarms" and sub.subscribe_alarms:
                should_send = True
                if "alarms" in sub.once_channels:
                    once_key = "alarms"
            elif channel == "metrics" and sub.subscribe_metrics:
                should_send = True
                if "metrics" in sub.once_channels:
                    once_key = "metrics"

            if should_send:
                # Check simple per-connection rate limit (client-requested)
                last = self._last_sent.get(ws, 0.0)
                if sub.min_interval_s > 0 and (now - last) < sub.min_interval_s:
                    # skip send for this connection due to rate limiting
                    continue
                tasks.append(asyncio.create_task(_send(ws)))
                if once_key is not None:
                    once_remove.append((ws, once_key))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if res is not None and not isinstance(res, Exception):
                    stale.append(res)

        # Remove once channels after successful send
        for ws, key in once_remove:
            sub = self._subscriptions.get(ws)
            if sub:
                sub.once_channels.discard(key)
                if key == "*":
                    sub.signal_names.discard("*")
                elif key == "alarms":
                    sub.subscribe_alarms = False
                elif key == "metrics":
                    sub.subscribe_metrics = False
                else:
                    sub.signal_names.discard(key)

        for ws in stale:
            await self.disconnect(ws)

    # ── Handle loops ─────────────────────────────────────────────────────────

    async def handle(self, ws: WebSocket, topics: set[SubscriptionTopic] | None = None) -> None:
        """Legacy handler: giữ kết nối sống cho /ws/signals, /ws/alarms, /ws/all."""
        await self.connect(ws, topics)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            logger.debug("WebSocket ngắt kết nối sạch sẽ")
        except Exception:
            logger.exception("WebSocket handler error")
        finally:
            await self.disconnect(ws)

    async def handle_subscribe(self, ws: WebSocket) -> None:
        """Handler cho /ws/subscribe — nhận lệnh subscribe/unsubscribe từ client."""
        await self.connect_subscribe(ws)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue
                await self.process_subscribe_command(ws, data)
        except WebSocketDisconnect:
            logger.debug("WS subscribe ngắt kết nối")
        except Exception:
            logger.exception("WS subscribe handler error")
        finally:
            await self.disconnect(ws)
