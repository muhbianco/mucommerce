from __future__ import annotations

from httpx import AsyncClient


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["X-Request-ID"]


async def test_readyz_reports_database(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["database"] is True
    assert body["redis"] is None


async def test_error_envelope_and_request_id_passthrough(client: AsyncClient) -> None:
    response = await client.get("/api/v1/ops/tenants", headers={"X-Request-ID": "abc-123"})
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "authentication_failed"
    assert body["error"]["request_id"] == "abc-123"
    assert response.headers["X-Request-ID"] == "abc-123"
