"""Security regression tests for authentication bypass attempts."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


@pytest.mark.asyncio
async def test_signals_endpoint_rejects_missing_api_key():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, api_key="test-key")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/signals")

    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_signals_endpoint_rejects_invalid_api_key():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, api_key="test-key")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/signals", headers={"X-API-Key": "wrong-key"})

    assert resp.status_code == 401
