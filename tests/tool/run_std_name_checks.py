"""Run quick end-to-end checks for std_name support (REST + WebSocket).

Usage: run with workspace venv python.
"""
import asyncio
import json

from httpx import AsyncClient
from httpx import ASGITransport
from starlette.testclient import TestClient

from src.api.app import create_app
from src.core.signal_store import SignalStore
from src.api.websocket import ConnectionManager


class FakeRepo:
    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []


class FakeWriter:
    def __init__(self):
        self.sent = []

    async def send_signal(self, signal_name, value):
        # accept anything
        self.sent.append((signal_name, value))


async def run_rest_checks():
    store = SignalStore()
    # set a canonical signal value
    await store.update("HMI_FL_OccupantAge_years", 21)
    app = create_app(store, FakeRepo(), api_key="")
    # attach fake writer so write endpoints work
    app.state.writer = FakeWriter()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Read by canonical
        resp_canon = await client.get("/signals/HMI_FL_OccupantAge_years")
        print("GET canonical status:", resp_canon.status_code)
        print(resp_canon.json())

        # Read by std_name (should resolve to canonical)
        resp_std = await client.get("/signals/HMI_FL_OccupantAge")
        print("GET std_name status:", resp_std.status_code)
        print(resp_std.json())

        # Write by canonical
        put = await client.put("/signals/HMI_FL_OccupantAge_years", json={"value": 25})
        print("PUT canonical status:", put.status_code)
        print(put.json())

        # Write by std_name
        put2 = await client.put("/signals/HMI_FL_OccupantAge", json={"value": 30})
        print("PUT std_name status:", put2.status_code)
        print(put2.json())

        # Inspect writer queue
        print("Writer recorded:", app.state.writer.sent)


def ws_checks_sync():
    # Use TestClient for sync WebSocket test against the ASGI app
    store = SignalStore()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.update("HMI_FL_OccupantAge_years", 22))
    app = create_app(store, FakeRepo(), api_key="")
    mgr: ConnectionManager = app.state.ws_manager

    with TestClient(app) as tc:
        # connect subscribe endpoint
        with tc.websocket_connect("/ws/subscribe") as ws:
            # subscribe using std_name
            ws.send_text(json.dumps({"action": "subscribe", "channels": ["HMI_FL_OccupantAge"], "mode": "continuous"}))
            # read ack
            ack = ws.receive_text()
            print("WS subscribe ack (std_name):", ack)

            # broadcast signal canonical
            loop.run_until_complete(mgr.broadcast_signal("HMI_FL_OccupantAge_years", 42.0, 1234567.0))
            payload = ws.receive_text()
            print("WS received (std_name subscribe):", payload)

        # Now subscribe using canonical name
        with tc.websocket_connect("/ws/subscribe") as ws2:
            ws2.send_text(json.dumps({"action": "subscribe", "channels": ["HMI_FL_OccupantAge_years"], "mode": "continuous"}))
            ack2 = ws2.receive_text()
            print("WS subscribe ack (canonical):", ack2)

            loop.run_until_complete(mgr.broadcast_signal("HMI_FL_OccupantAge_years", 43.0, 1234568.0))
            payload2 = ws2.receive_text()
            print("WS received (canonical subscribe):", payload2)


if __name__ == "__main__":
    asyncio.run(run_rest_checks())
    ws_checks_sync()
