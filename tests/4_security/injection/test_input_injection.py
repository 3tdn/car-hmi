"""Security tests for injection-like payload handling."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []

    async def query_alarms(self, **_):
        return []


@pytest.mark.asyncio
async def test_signal_lookup_with_injection_payload_does_not_crash():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, _FakeRepo(), api_key="test-key")

    payload = "' OR 1=1 --"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/signals/{payload}", headers={"X-API-Key": "test-key"})

    # Depending on profile/authz flow this may be 403 or 404, but must never be 5xx.
    assert resp.status_code < 500
