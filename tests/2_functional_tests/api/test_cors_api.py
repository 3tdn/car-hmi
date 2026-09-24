"""CORS policy tests for exact and dynamic frontend origins."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app
from src.core.signal_store import SignalStore


class _FakeRepo:
    async def query_signals(self, **_):
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize("wildcard", ["*", "x"])
async def test_cors_origins_allows_ipv4_wildcards_on_port_5173(wildcard):
    app = create_app(
        SignalStore(),
        _FakeRepo(),
        cors_origins=[
            "http://localhost:5173",
            f"http://192.168.{wildcard}.{wildcard}:5173",
        ],
    )
    headers = {
        "Origin": "http://192.168.27.41:5173",
        "Access-Control-Request-Method": "GET",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options("/api/info", headers=headers)
        exact_response = await client.options(
            "/api/info",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == headers["Origin"]
    assert exact_response.status_code == 200
    assert exact_response.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://192.169.27.41:5173",
        "http://192.168.27.41:8000",
        "https://192.168.27.41:5173",
        "http://10.0.0.41:5173",
        "http://192.168.999.41:5173",
    ],
)
async def test_cors_origins_wildcard_rejects_other_subnets_ports_and_schemes(origin):
    app = create_app(
        SignalStore(),
        _FakeRepo(),
        cors_origins=["http://192.168.*.*:5173"],
    )
    headers = {"Origin": origin, "Access-Control-Request-Method": "GET"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options("/api/info", headers=headers)

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers
