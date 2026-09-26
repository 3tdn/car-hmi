"""Integration tests for REST API endpoints."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


class _FakeReader:
    def __init__(self, *, thread_alive: bool, last_frame_timestamp: float, fatal_error: str | None = None):
        self._state = {
            "thread_alive": thread_alive,
            "last_frame_timestamp": last_frame_timestamp,
            "fatal_error": fatal_error,
        }

    def get_runtime_state(self):
        return dict(self._state)


class _FakeWriter:
    def __init__(self):
        self.writes: list[tuple[str, float]] = []

    async def send_signal(self, signal_name, value):
        self.writes.append((signal_name, value))

    async def send_signals_batch(self, values):
        for signal_name, value in values.items():
            self.writes.append((signal_name, value))
        return values, []


def _write_profiles(path, *, active, profiles, client_sessions=None, sessions_path=None):
    payload = {
        "active": active,
        "profiles": profiles,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    if sessions_path is not None:
        sessions_payload = {"client_sessions": client_sessions or {}}
        sessions_path.write_text(json.dumps(sessions_payload), encoding="utf-8")


@pytest_asyncio.fixture
async def client():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, api_key="test-key")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio

async def test_list_signals_no_auth(client):
    resp = await client.get("/signals")
    assert resp.status_code == 401

async def test_list_signals_with_auth(client):
    resp = await client.get("/signals", headers={"X-API-Key": "test-key"})
    assert resp.status_code == 200
    data = resp.json()
    # RX values remain readable with a valid API key and no profile header.
    assert data["total"] == 1
    assert data["items"][0]["signal_name"] == "VehicleSpeed"
    assert data["items"][0]["value"] == 60.0
    assert data["warnings"] == []

def test_api_key_auth_verify_valid():
    """verify() returns True for correct key."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("my-secret")
    assert auth.verify("my-secret") is True

def test_api_key_auth_verify_invalid():
    """verify() returns False for wrong or missing key."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("my-secret")
    assert auth.verify("wrong") is False
    assert auth.verify(None) is False
    assert auth.verify("") is False

def test_api_key_auth_verify_disabled():
    """verify() always returns True when auth is disabled (empty key)."""
    from src.api.auth import APIKeyAuth

    auth = APIKeyAuth("")
    assert auth.verify(None) is True
    assert auth.verify("anything") is True
