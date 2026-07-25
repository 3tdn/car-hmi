"""Quản lý kết nối WebSocket để push tín hiệu và cảnh báo thời gian thực.

Hỗ trợ:
- Topic-based subscription cũ (backward-compat): /ws/signals, /ws/alarms, /ws/all
- Per-signal subscription mới: /ws/subscribe — client gửi JSON command để chọn kênh
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect

from src.api.routes.profiles import (
    build_access_warning,
    get_profile_context,
    profile_allows_signal,
    profile_has_permission,
    profile_signal_names,
)
from src.core.signal_name_mapper import SignalNameMapper

logger = logging.getLogger(__name__)


class SubscriptionTopic(str, Enum):
    SIGNALS = "signals"
    ALARMS = "alarms"
    ALL = "all"


class _ClientSubscription:
    """State riêng cho 1 WS connection dùng giao thức subscribe mới."""

    __slots__ = ("signal_names", "subscribe_alarms", "subscribe_metrics", "once_channels", "min_interval_s", "profile_name")

    def __init__(self) -> None:
        self.signal_names: set[str] = set()  # rỗng = không nhận signal nào; "*" = tất cả
        self.subscribe_alarms: bool = False
        self.subscribe_metrics: bool = False
        # Channels đã yêu cầu mode "once" — sẽ bị gỡ sau khi gửi lần đầu
        self.once_channels: set[str] = set()

        # If > 0, minimum seconds between sends to this connection (client-requested)
        self.min_interval_s: float = 0.0
        self.profile_name: str | None = None

    def wants_signal(self, name: str) -> bool:
        if "*" in self.signal_names:
            return True
        return name in self.signal_names


class ConnectionManager:
    """Quản lý các kết nối WebSocket đang hoạt động và phát sóng fan-out."""

    def __init__(self, signal_name_mapper: SignalNameMapper | None = None) -> None:
        # Legacy topic-based connections
        self._connections: dict[WebSocket, set[SubscriptionTopic]] = {}
        # New per-signal subscription connections
        self._subscriptions: dict[WebSocket, _ClientSubscription] = {}
        # Track last send time per websocket + stream key for rate-limiting.
        # Key format: (ws, "sig:<name>") or (ws, "ch:<alarms|metrics>").
        self._last_sent: dict[tuple[WebSocket, str], float] = {}
        # Latest signal values for building full subscribed payloads.
        self._latest_signals: dict[str, dict] = {}
        self._only_send_signal_update: bool = False
        self._lock = asyncio.Lock()
        self._mapper: SignalNameMapper = signal_name_mapper or SignalNameMapper()

    def set_only_send_signal_update(self, enabled: bool) -> None:
        """Control WS signal payload mode for subscribe connections.

        False: send full subscribed snapshot (from latest cache).
        True: send only changed signals from current batch.
        """
        self._only_send_signal_update = bool(enabled)

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
            # Cleanup per-stream rate-limit state for this websocket
            stale_rate_keys = [key for key in self._last_sent if key[0] is ws]
            for key in stale_rate_keys:
                self._last_sent.pop(key, None)
        logger.debug("WS đã ngắt kết nối — tổng số: %d", len(self._connections) + len(self._subscriptions))

    async def close_all(self) -> None:
        """Đóng toàn bộ kết nối WebSocket đang mở khi ứng dụng shutdown."""
        current_task = asyncio.current_task()
        async with self._lock:
            sockets = list(self._connections) + list(self._subscriptions)
        for ws in sockets:
            try:
                await asyncio.shield(ws.close(code=1001))
            except asyncio.CancelledError:
                if current_task is not None:
                    current_task.uncancel()
            except Exception:
                pass
            try:
                await asyncio.shield(self.disconnect(ws))
            except asyncio.CancelledError:
                if current_task is not None:
                    current_task.uncancel()
            except Exception:
                pass

    # ── New subscribe-based connect ──────────────────────────────────────────

    async def connect_subscribe(self, ws: WebSocket, profile_name: str | None = None) -> None:
        """Accept WS connection cho giao thức subscribe mới."""
        await ws.accept()
        async with self._lock:
            sub = _ClientSubscription()
            sub.profile_name = profile_name
            self._subscriptions[ws] = sub
        logger.debug("WS subscribe đã kết nối — tổng số: %d", len(self._subscriptions))

    def _get_sub(self, ws: WebSocket) -> _ClientSubscription | None:
        return self._subscriptions.get(ws)

    async def process_subscribe_command(self, ws: WebSocket, data: dict) -> None:
        """Xử lý lệnh subscribe/unsubscribe từ client.

        Chấp nhận cả 2 định dạng:
        - Demo format: {"type": "subscribe", "signals": ["name", "*", "alarms", "metrics"]}
        - Legacy format: {"action": "subscribe", "channels": ["name"], "mode": "continuous"}
        """
        # Normalize: demo format (type/signals) hoặc legacy (action/channels)
        msg_type = data.get("type", "")
        if msg_type in ("subscribe", "unsubscribe"):
            action = msg_type
            raw_ch = data.get("signals", data.get("channels", []))
        else:
            action = data.get("action", "subscribe")
            raw_ch = data.get("channels", data.get("signals", []))
        # signals có thể là string "*" hoặc list
        channels = [raw_ch] if isinstance(raw_ch, str) else list(raw_ch)
        mode = data.get("mode", "continuous")
        # Optional per-connection rate limiting requested by client (ms)
        rate_ms = data.get("rate_ms")

        accepted_channels: list[str] = []
        warnings: list[dict] = []

        async with self._lock:
            sub = self._get_sub(ws)
            if sub is None:
                return

            profile_name: str | None = None
            profile: dict | None = None
            try:
                profile_name, profile, _ = get_profile_context(sub.profile_name, allow_bootstrap=True)
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                if isinstance(detail, dict):
                    warnings.append(detail)
                else:
                    warnings.append(build_access_warning("profile_access_error", str(exc)))

            has_read_permission = profile is None or profile_has_permission(profile, "read")
            if profile is not None and not has_read_permission:
                warnings.append(
                    build_access_warning(
                        "profile_permission_denied",
                        f"Profile '{profile_name}' lacks 'read' permission",
                        profile_name=profile_name,
                        required_permission="read",
                    )
                )

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
                        if not has_read_permission:
                            continue
                        sub.subscribe_alarms = True
                        accepted_channels.append("alarms")
                    elif ch_lower == "metrics":
                        if not has_read_permission:
                            continue
                        sub.subscribe_metrics = True
                        accepted_channels.append("metrics")
                    elif ch == "*":
                        if not has_read_permission:
                            continue
                        if profile is None:
                            sub.signal_names.add("*")
                            accepted_channels.append("*")
                        else:
                            allowed: list[str] = []
                            for signal_name in profile_signal_names(profile, required="read"):
                                canonical = self._mapper.resolve(signal_name)
                                sub.signal_names.add(canonical)
                                allowed.append(canonical)
                            accepted_channels.extend(allowed)
                            warnings.append(
                                build_access_warning(
                                    "profile_signal_filtered",
                                    f"Wildcard subscription limited to profile '{profile_name}' signals",
                                    profile_name=profile_name,
                                    required_permission="read",
                                    signals=sorted(allowed),
                                )
                            )
                    else:
                        if not has_read_permission:
                            continue
                        # Resolve std_name -> canonical signal_name before storing
                        canonical = self._mapper.resolve(ch)
                        std_name = self._mapper.get_std_name(canonical) or ch
                        if profile is not None and not profile_allows_signal(profile, canonical, [ch, std_name], required="read"):
                            warnings.append(
                                build_access_warning(
                                    "profile_signal_denied",
                                    f"Signal '{canonical}' is outside profile '{profile_name}' scope",
                                    profile_name=profile_name,
                                    required_permission="read",
                                    signal_name=canonical,
                                )
                            )
                            continue
                        sub.signal_names.add(canonical)
                        accepted_channels.append(canonical)

                    if mode == "once":
                        if ch == "*":
                            sub.once_channels.update(accepted_channels)
                        elif ch in {"alarms", "metrics"}:
                            sub.once_channels.add(ch)
                        else:
                            sub.once_channels.add(self._mapper.resolve(ch))
                elif action == "unsubscribe":
                    if ch_lower == "alarms":
                        sub.subscribe_alarms = False
                    elif ch_lower == "metrics":
                        sub.subscribe_metrics = False
                    elif ch == "*":
                        sub.signal_names.discard("*")
                    else:
                        sub.signal_names.discard(self._mapper.resolve(ch))

        # Ack — normalized format expected by tests: {"type":"<action>_ack","action":...,"channels":[...]}.
        ack_type = f"{action}_ack"
        ack_payload = json.dumps({
            "type": ack_type,
            "action": action,
            "channels": accepted_channels if action == "subscribe" else channels,
            "count": len(accepted_channels if action == "subscribe" else channels),
            "warnings": warnings,
        })
        try:
            await ws.send_text(ack_payload)
        except Exception:
            pass

    # ── Broadcast ────────────────────────────────────────────────────────────

    async def broadcast_signal(self, signal_name: str, value: float, timestamp: float) -> None:
        """Push signal frame theo demo format: {"timestamp": ISO8601, "signals": [{name, std_name, value}]}."""
        await self.broadcast_signals([(signal_name, value, timestamp)])

    async def broadcast_signals(self, updates: list[tuple[str, float, float]]) -> None:
        """Push 1 WS frame chứa nhiều signal entries: {timestamp, signals:[{name,std_name,value}, ...]}."""
        if not updates:
            return

        import datetime

        latest_ts = max(ts for _, _, ts in updates)
        iso_ts = datetime.datetime.fromtimestamp(latest_ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Last-write-wins theo signal_name trong cùng một batch.
        merged: dict[str, dict] = {}
        for signal_name, value, _ in updates:
            std = self._mapper.get_std_name(signal_name) or signal_name
            entry = {
                "name": signal_name,
                "std_name": std,
                "value": value,
            }
            merged[signal_name] = entry
            self._latest_signals[signal_name] = entry

        entries = list(merged.values())

        # Legacy/topic WS clients always receive full batch.
        await self._broadcast(
            json.dumps({"timestamp": iso_ts, "signals": entries}),
            SubscriptionTopic.SIGNALS,
        )
        # Subscribe WS clients receive filtered batch according to subscription.
        await self._broadcast_signal_batch_to_subscribers(entries, iso_ts)

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

        async def _send(ws: WebSocket, rate_key: str | None = None) -> WebSocket | None:
            try:
                await ws.send_text(text)
                if rate_key is not None:
                    self._last_sent[(ws, rate_key)] = asyncio.get_event_loop().time()
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
                if signal_name is not None:
                    rate_key = f"sig:{signal_name}"
                else:
                    rate_key = f"ch:{channel}"
                last = self._last_sent.get((ws, rate_key), 0.0)
                if sub.min_interval_s > 0 and (now - last) < sub.min_interval_s:
                    # skip send for this connection due to rate limiting
                    continue
                tasks.append(asyncio.create_task(_send(ws, rate_key)))
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

    async def _broadcast_signal_batch_to_subscribers(
        self,
        entries: list[dict],
        iso_ts: str,
    ) -> None:
        """Broadcast signal batch tới subscribe-based WS theo filter từng kết nối."""
        stale: list[WebSocket] = []
        async with self._lock:
            snapshot = list(self._subscriptions.items())

        async def _send(ws: WebSocket, text: str) -> WebSocket | None:
            try:
                await ws.send_text(text)
                self._last_sent[(ws, "sig:batch")] = asyncio.get_event_loop().time()
                return None
            except Exception:
                return ws

        tasks = []
        once_remove: list[tuple[WebSocket, str]] = []
        now = asyncio.get_event_loop().time()
        changed_names = {
            e.get("name")
            for e in entries
            if isinstance(e.get("name"), str)
        }

        for ws, sub in snapshot:
            if not changed_names:
                continue

            if "*" in sub.signal_names:
                has_relevant_change = True
            else:
                has_relevant_change = bool(changed_names.intersection(sub.signal_names))

            if not has_relevant_change:
                continue

            # Respect per-connection min interval for signal stream.
            last = self._last_sent.get((ws, "sig:batch"), 0.0)
            if sub.min_interval_s > 0 and (now - last) < sub.min_interval_s:
                continue

            if self._only_send_signal_update:
                # Changed-only mode.
                if "*" in sub.signal_names:
                    selected = [e for e in entries if e.get("name") in changed_names]
                else:
                    selected = [
                        e for e in entries
                        if isinstance(e.get("name"), str) and e.get("name") in sub.signal_names
                    ]
            else:
                # Full-snapshot mode.
                if "*" in sub.signal_names:
                    selected = list(self._latest_signals.values())
                else:
                    selected = [
                        self._latest_signals[name]
                        for name in sub.signal_names
                        if name in self._latest_signals
                    ]

            if not selected:
                continue

            payload = json.dumps({"timestamp": iso_ts, "signals": selected})
            tasks.append(asyncio.create_task(_send(ws, payload)))

            # once-mode cleanup keys for signals actually sent.
            if "*" in sub.once_channels:
                once_remove.append((ws, "*"))
            for e in selected:
                name = e.get("name")
                if isinstance(name, str) and name in sub.once_channels:
                    once_remove.append((ws, name))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for res in results:
                if res is not None and not isinstance(res, Exception):
                    stale.append(res)

        for ws, key in once_remove:
            sub = self._subscriptions.get(ws)
            if sub:
                sub.once_channels.discard(key)
                if key == "*":
                    sub.signal_names.discard("*")
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

    async def handle_subscribe(self, ws: WebSocket, profile_name: str | None = None) -> None:
        """Handler cho /ws/subscribe — nhận lệnh subscribe/unsubscribe từ client."""
        await self.connect_subscribe(ws, profile_name=profile_name)
        try:
            while True:
                raw = await ws.receive_text()
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                    continue
                if data.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
                else:
                    await self.process_subscribe_command(ws, data)
        except WebSocketDisconnect:
            logger.debug("WS subscribe ngắt kết nối")
        except Exception:
            logger.exception("WS subscribe handler error")
        finally:
            await self.disconnect(ws)
