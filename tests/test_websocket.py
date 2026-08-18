"""WebSocket ConnectionManager tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from src.api.websocket import ConnectionManager, SubscriptionTopic


class FakeWebSocket:
    """Minimal mock of fastapi.WebSocket for unit testing."""

    def __init__(self):
        self.accepted = False
        self.sent: list[str] = []
        self.closed = False
        self._recv_queue: asyncio.Queue[str] = asyncio.Queue()
        self._should_disconnect = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, data: str):
        if self._should_disconnect:
            raise RuntimeError("connection closed")
        self.sent.append(data)

    async def receive_text(self) -> str:
        from fastapi import WebSocketDisconnect

        while True:
            if self._should_disconnect:
                raise WebSocketDisconnect(code=1000)
            try:
                return self._recv_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.01)

    def force_disconnect(self):
        self._should_disconnect = True


@pytest.fixture
def mgr():
    return ConnectionManager()


@pytest.mark.asyncio
async def test_connect_and_disconnect(mgr):
    ws = FakeWebSocket()
    await mgr.connect(ws)
    assert ws.accepted
    assert len(mgr._connections) == 1
    await mgr.disconnect(ws)
    assert len(mgr._connections) == 0


@pytest.mark.asyncio
async def test_broadcast_signal_no_connections(mgr):
    # Should be a no-op, not raise
    await mgr.broadcast_signal("Speed", 80.0, 1234.0)


@pytest.mark.asyncio
async def test_broadcast_signal_to_subscribers(mgr):
    ws_all = FakeWebSocket()
    ws_signals = FakeWebSocket()
    ws_alarms = FakeWebSocket()

    await mgr.connect(ws_all, {SubscriptionTopic.ALL})
    await mgr.connect(ws_signals, {SubscriptionTopic.SIGNALS})
    await mgr.connect(ws_alarms, {SubscriptionTopic.ALARMS})

    await mgr.broadcast_signal("Speed", 80.0, 1000.0)

    # ALL and SIGNALS should receive, ALARMS should not
    assert len(ws_all.sent) == 1
    assert len(ws_signals.sent) == 1
    assert len(ws_alarms.sent) == 0

    payload = json.loads(ws_all.sent[0])
    assert "timestamp" in payload
    assert isinstance(payload.get("signals"), list)
    assert len(payload["signals"]) == 1
    sig = payload["signals"][0]
    assert set(sig.keys()) == {"name", "std_name", "value"}
    assert sig["name"] == "Speed"
    assert sig["std_name"] == "Speed"
    assert sig["value"] == 80.0


@pytest.mark.asyncio
async def test_broadcast_alarm_to_subscribers(mgr):
    ws_all = FakeWebSocket()
    ws_signals = FakeWebSocket()
    ws_alarms = FakeWebSocket()

    await mgr.connect(ws_all, {SubscriptionTopic.ALL})
    await mgr.connect(ws_signals, {SubscriptionTopic.SIGNALS})
    await mgr.connect(ws_alarms, {SubscriptionTopic.ALARMS})

    await mgr.broadcast_alarm({"signal": "CoolantTemp", "level": "critical", "value": 115.0})

    # ALL and ALARMS should receive, SIGNALS should not
    assert len(ws_all.sent) == 1
    assert len(ws_alarms.sent) == 1
    assert len(ws_signals.sent) == 0

    payload = json.loads(ws_alarms.sent[0])
    assert payload["type"] == "alarm"
    assert payload["signal"] == "CoolantTemp"


@pytest.mark.asyncio
async def test_stale_connection_removed_on_broadcast(mgr):
    ws_good = FakeWebSocket()
    ws_bad = FakeWebSocket()
    ws_bad.force_disconnect()

    await mgr.connect(ws_good)
    await mgr.connect(ws_bad)
    assert len(mgr._connections) == 2

    await mgr.broadcast_signal("RPM", 3000.0, 1000.0)

    # Bad connection removed, good one still there
    assert len(mgr._connections) == 1
    assert len(ws_good.sent) == 1


@pytest.mark.asyncio
async def test_default_subscription_is_all(mgr):
    ws = FakeWebSocket()
    await mgr.connect(ws)  # no explicit topics
    assert SubscriptionTopic.ALL in mgr._connections[ws]


@pytest.mark.asyncio
async def test_disconnect_idempotent(mgr):
    ws = FakeWebSocket()
    await mgr.connect(ws)
    await mgr.disconnect(ws)
    await mgr.disconnect(ws)  # should not raise
    assert len(mgr._connections) == 0


# ── Subscribe protocol tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscribe_connect_and_disconnect(mgr):
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    assert ws.accepted
    assert ws in mgr._subscriptions
    await mgr.disconnect(ws)
    assert ws not in mgr._subscriptions


@pytest.mark.asyncio
async def test_subscribe_signal_filter(mgr):
    """Subscribed signals should be filtered — only matching names get sent."""
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)

    # Subscribe only to "Speed"
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["Speed"],
        "mode": "continuous",
    })

    # Clear the ack message
    ws.sent.clear()

    # Broadcast Speed → should receive
    await mgr.broadcast_signal("Speed", 80.0, 1000.0)
    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert payload["signals"][0]["name"] == "Speed"

    # Broadcast RPM → should NOT receive
    await mgr.broadcast_signal("RPM", 3000.0, 1001.0)
    assert len(ws.sent) == 1  # still 1


@pytest.mark.asyncio
async def test_subscribe_wildcard(mgr):
    """'*' subscribes to all signals."""
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["*"],
        "mode": "continuous",
    })
    ws.sent.clear()

    await mgr.broadcast_signal("Speed", 80.0, 1000.0)
    await mgr.broadcast_signal("RPM", 3000.0, 1001.0)
    assert len(ws.sent) == 2


@pytest.mark.asyncio
async def test_subscribe_alarms_channel(mgr):
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["alarms"],
        "mode": "continuous",
    })
    ws.sent.clear()

    await mgr.broadcast_alarm({"signal": "Temp", "level": "warning"})
    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert payload["type"] == "alarm"


@pytest.mark.asyncio
async def test_subscribe_metrics_channel(mgr):
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["metrics"],
        "mode": "continuous",
    })
    ws.sent.clear()

    await mgr.broadcast_metrics({"cpu_percent": 23.4, "ram_percent": 41.0})
    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert payload["type"] == "metrics"
    assert payload["cpu_percent"] == 23.4


@pytest.mark.asyncio
async def test_unsubscribe(mgr):
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["Speed", "RPM"],
        "mode": "continuous",
    })
    ws.sent.clear()

    # Unsubscribe from RPM
    await mgr.process_subscribe_command(ws, {
        "action": "unsubscribe",
        "channels": ["RPM"],
    })
    ws.sent.clear()

    await mgr.broadcast_signal("Speed", 80.0, 1000.0)
    await mgr.broadcast_signal("RPM", 3000.0, 1001.0)
    assert len(ws.sent) == 1  # only Speed


@pytest.mark.asyncio
async def test_subscribe_once_mode(mgr):
    """Once mode: signal delivered once, then auto-unsubscribed."""
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["Temp"],
        "mode": "once",
    })
    ws.sent.clear()

    # First broadcast → received
    await mgr.broadcast_signal("Temp", 85.0, 1000.0)
    assert len(ws.sent) == 1

    # Second broadcast → NOT received (auto-unsubscribed)
    await mgr.broadcast_signal("Temp", 86.0, 1001.0)
    assert len(ws.sent) == 1


@pytest.mark.asyncio
async def test_subscribe_ack(mgr):
    """Subscribe command should return an ack message."""
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "action": "subscribe",
        "channels": ["Speed"],
        "mode": "continuous",
    })
    assert len(ws.sent) == 1
    ack = json.loads(ws.sent[0])
    assert ack["type"] == "subscribe_ack"
    assert ack["action"] == "subscribe"
    assert ack["channels"] == ["Speed"]


@pytest.mark.asyncio
async def test_subscribe_no_leak_to_legacy(mgr):
    """Legacy and subscribe connections are independent."""
    ws_legacy = FakeWebSocket()
    ws_sub = FakeWebSocket()

    await mgr.connect(ws_legacy, {SubscriptionTopic.ALL})
    await mgr.connect_subscribe(ws_sub)
    await mgr.process_subscribe_command(ws_sub, {
        "action": "subscribe",
        "channels": ["Speed"],
        "mode": "continuous",
    })
    ws_sub.sent.clear()

    await mgr.broadcast_signal("Speed", 80.0, 1000.0)
    # Legacy gets ALL, subscribe gets only Speed
    assert len(ws_legacy.sent) == 1
    assert len(ws_sub.sent) == 1

    await mgr.broadcast_signal("RPM", 3000.0, 1001.0)
    # Legacy gets ALL, subscribe does NOT (not subscribed to RPM)
    assert len(ws_legacy.sent) == 2
    assert len(ws_sub.sent) == 1


@pytest.mark.asyncio
async def test_broadcast_signals_batch_single_frame(mgr):
    """Multiple signals should be delivered in one WS frame with signals[] entries."""
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "type": "subscribe",
        "signals": ["Speed", "RPM"],
        "mode": "continuous",
    })
    ws.sent.clear()  # drop ack

    await mgr.broadcast_signals([
        ("Speed", 80.0, 1000.0),
        ("RPM", 3000.0, 1000.1),
    ])

    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert "timestamp" in payload
    assert isinstance(payload.get("signals"), list)
    assert len(payload["signals"]) == 2
    names = {s["name"] for s in payload["signals"]}
    assert names == {"Speed", "RPM"}
    for s in payload["signals"]:
        assert set(s.keys()) == {"name", "std_name", "value"}


@pytest.mark.asyncio
async def test_subscribe_receives_full_subscribed_set_from_cache(mgr):
    """After cache is warm, each push includes all subscribed signals, not only changed ones."""
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "type": "subscribe",
        "signals": ["Speed", "RPM"],
        "mode": "continuous",
    })
    ws.sent.clear()  # drop ack

    # Warm cache with both signals.
    await mgr.broadcast_signals([
        ("Speed", 80.0, 1000.0),
        ("RPM", 3000.0, 1000.1),
    ])
    ws.sent.clear()

    # Change only one signal; payload should still contain full subscribed set.
    await mgr.broadcast_signals([
        ("Speed", 81.0, 1000.2),
    ])

    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert isinstance(payload.get("signals"), list)
    names = {s["name"] for s in payload["signals"]}
    assert names == {"Speed", "RPM"}
    speed = next(s for s in payload["signals"] if s["name"] == "Speed")
    rpm = next(s for s in payload["signals"] if s["name"] == "RPM")
    assert speed["value"] == 81.0
    assert rpm["value"] == 3000.0


@pytest.mark.asyncio
async def test_subscribe_changed_only_mode(mgr):
    """When only_send_signal_update=True, payload contains only changed subscribed signals."""
    mgr.set_only_send_signal_update(True)

    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    await mgr.process_subscribe_command(ws, {
        "type": "subscribe",
        "signals": ["Speed", "RPM"],
        "mode": "continuous",
    })
    ws.sent.clear()

    await mgr.broadcast_signals([
        ("Speed", 81.0, 1000.2),
    ])

    assert len(ws.sent) == 1
    payload = json.loads(ws.sent[0])
    assert isinstance(payload.get("signals"), list)
    assert len(payload["signals"]) == 1
    sig = payload["signals"][0]
    assert sig["name"] == "Speed"
    assert sig["value"] == 81.0


@pytest.mark.asyncio
async def test_has_signal_interest_false_when_no_connections(mgr):
    assert await mgr.has_signal_interest({"COM_Status_PumaFLEthernet"}) is False


@pytest.mark.asyncio
async def test_has_signal_interest_true_for_legacy_signals_topic(mgr):
    ws = FakeWebSocket()
    await mgr.connect(ws, {SubscriptionTopic.SIGNALS})
    assert await mgr.has_signal_interest({"COM_Status_PumaFLEthernet"}) is True


@pytest.mark.asyncio
async def test_has_signal_interest_true_for_matching_subscribe_signal(mgr):
    ws = FakeWebSocket()
    await mgr.connect_subscribe(ws)
    mgr._subscriptions[ws].signal_names.add("COM_Status_PumaFLEthernet")
    assert await mgr.has_signal_interest({"COM_Status_PumaFLEthernet"}) is True
    assert await mgr.has_signal_interest({"COM_Status_PumaFREthernet"}) is False
