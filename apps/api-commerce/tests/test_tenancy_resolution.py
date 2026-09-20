from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tenancy.models import TenantStatus
from tests.conftest import create_tenant


async def test_platform_subdomain_resolves_tenant(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await create_tenant(session_factory, "lunares")
    response = await client.get("/api/v1/storefront/context", headers={"host": "lunares.loja.test"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant"]["id"] == tenant.id
    assert body["primary_host"] == "lunares.loja.test"
    assert body["access_mode"] == "whitelist"
    assert "payments.mercadopago" not in body["features"]


async def test_unknown_host_is_404_without_leaking(client: AsyncClient) -> None:
    response = await client.get("/api/v1/storefront/context", headers={"host": "evil.example"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "tenant_not_found"


async def test_host_with_port_and_case_is_normalized(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_tenant(session_factory, "brownie")
    response = await client.get(
        "/api/v1/storefront/context", headers={"host": "Brownie.LOJA.test:443"}
    )
    assert response.status_code == 200


async def test_draft_tenant_is_not_served(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_tenant(session_factory, "rascunho", active=False)
    response = await client.get(
        "/api/v1/storefront/context", headers={"host": "rascunho.loja.test"}
    )
    assert response.status_code == 404


async def test_suspended_tenant_returns_503(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await create_tenant(session_factory, "pausada")
    async with session_factory() as session:
        row = await session.get(type(tenant), tenant.id)
        assert row is not None
        row.status = TenantStatus.SUSPENDED
        await session.commit()
    response = await client.get("/api/v1/storefront/context", headers={"host": "pausada.loja.test"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "tenant_suspended"


async def test_internal_context_requires_token_and_honours_tenant_host(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_tenant(session_factory, "lunares")

    missing = await client.get(
        "/api/v1/internal/storefront/context",
        headers={"host": "api.test", "X-Tenant-Host": "lunares.loja.test"},
    )
    assert missing.status_code == 401

    ok = await client.get(
        "/api/v1/internal/storefront/context",
        headers={
            "host": "api.test",
            "X-Tenant-Host": "lunares.loja.test",
            "X-Internal-Token": "web-token-test",
        },
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["tenant"]["slug"] == "lunares"
    assert "payments.mercadopago" in ok.json()["features"]


async def test_browser_cannot_spoof_tenant_via_x_tenant_host(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_tenant(session_factory, "lunares")
    await create_tenant(session_factory, "outra")
    # Public endpoint: X-Tenant-Host without the internal token is ignored; Host wins.
    response = await client.get(
        "/api/v1/storefront/context",
        headers={"host": "outra.loja.test", "X-Tenant-Host": "lunares.loja.test"},
    )
    assert response.status_code == 200
    assert response.json()["tenant"]["slug"] == "outra"


async def test_traefik_edge_config_lists_active_hosts(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_tenant(session_factory, "lunares")
    denied = await client.get("/api/v1/internal/edge/traefik", headers={"host": "api.test"})
    assert denied.status_code == 401

    response = await client.get(
        "/api/v1/internal/edge/traefik",
        headers={"host": "api.test", "X-Internal-Token": "traefik-token-test"},
    )
    assert response.status_code == 200, response.text
    rules = [r["rule"] for r in response.json()["http"]["routers"].values()]
    assert "Host(`lunares.loja.test`)" in rules
    etag = response.headers["ETag"]

    cached = await client.get(
        "/api/v1/internal/edge/traefik",
        headers={
            "host": "api.test",
            "X-Internal-Token": "traefik-token-test",
            "If-None-Match": etag,
        },
    )
    assert cached.status_code == 304
