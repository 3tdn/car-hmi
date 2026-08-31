"""Run quick end-to-end checks for signal_name/std_name identity behavior.

Usage: run with workspace venv python.
"""
import asyncio
import json
import tempfile
from pathlib import Path

from httpx import AsyncClient
from httpx import ASGITransport
from starlette.testclient import TestClient

from src.api.app import create_app
from src.api.routes import profiles as profile_routes
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


def _disable_profiles_for_check():
    profiles_path = Path(tempfile.gettempdir()) / "car_hmi_empty_profiles_for_std_name_checks.json"
    profiles_path.write_text('{"active": null, "profiles": {}}', encoding="utf-8")
    profile_routes.PROFILES_PATH = profiles_path


async def run_rest_checks():
    _disable_profiles_for_check()
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

        # Write by canonical
        put = await client.put("/signals/HMI_FL_OccupantAge_years", json={"value": 25})
        print("PUT canonical status:", put.status_code)
        print(put.json())

        # Inspect writer queue
        print("Writer recorded:", app.state.writer.sent)


def ws_checks_sync():
    _disable_profiles_for_check()
    # Use TestClient for sync WebSocket test against the ASGI app
    store = SignalStore()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(store.update("HMI_FL_OccupantAge_years", 22))
    app = create_app(store, FakeRepo(), api_key="")
    mgr: ConnectionManager = app.state.ws_manager

    with TestClient(app) as tc:
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
