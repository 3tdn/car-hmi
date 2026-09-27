"""Security tests for injection-like payload handling."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


@pytest.mark.parametrize("value", ['"NaN"', '"Infinity"', '1e999', 'NaN', '-Infinity'])
@pytest.mark.parametrize("batch", [False, True])
async def test_nonfinite_write_returns_validation_error_without_calling_writer(value, batch):
    from unittest.mock import AsyncMock

    app = create_app(SignalStore(), api_key="test-key")
    writer = AsyncMock()
    app.state.writer = writer
    headers = {"X-API-Key": "test-key", "X-Dev-Mode": "true", "Content-Type": "application/json"}
    body = '{"value":' + value + '}'
    method, url = "PUT", "/signals/Speed"
    if batch:
        body = '{"signals":[{"signal_name":"Speed","value":' + value + '}]}'
        method, url = "POST", "/signals/batch_update"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, url, headers=headers, content=body)
    assert response.status_code == 422
    writer.send_signal.assert_not_awaited()
    writer.send_signals_batch.assert_not_awaited()


def test_unicode_api_key_does_not_raise_type_error():
    from src.api.auth import APIKeyAuth

    assert APIKeyAuth("secret").verify("khóa") is False
    assert APIKeyAuth("khóa").verify("khóa") is True


@pytest.mark.parametrize("field", ["Velocity", "Weight", "Height", "Distance"])
async def test_nonfinite_chart_filter_is_rejected_before_integer_conversion(field):
    app = create_app(SignalStore())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/adaptive_restraint/chart_info", params={field: "inf"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_signal_lookup_with_injection_payload_does_not_crash():
    store = SignalStore()
    await store.update("VehicleSpeed", 60.0)
    app = create_app(store, api_key="test-key")

    payload = "' OR 1=1 --"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/signals/{payload}", headers={"X-API-Key": "test-key"})

    # Depending on profile/authz flow this may be 403 or 404, but must never be 5xx.
    assert resp.status_code < 500
