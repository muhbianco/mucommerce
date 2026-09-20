from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import seed_platform_tenant
from app.core.config import settings
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import DomainRole, DomainStatus, TenantDomain


async def test_seed_platform_creates_primary_and_alias(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "platform_tenant_slug", "muhbianco")
    monkeypatch.setattr(settings, "platform_base_domain", "loja.test")
    monkeypatch.setattr(
        settings,
        "platform_alias_hosts",
        "staging.loja.test",
    )

    async with session_factory() as session:
        assert await seed_platform_tenant(session) == 0
    async with session_factory() as session:
        assert await seed_platform_tenant(session) == 0

    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(TenantDomain).execution_options(**{CROSS_TENANT_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
    by_host = {row.hostname: row for row in rows}
    assert by_host["loja.test"].role == DomainRole.PRIMARY
    assert by_host["loja.test"].status == DomainStatus.ACTIVE
    assert by_host["staging.loja.test"].role == DomainRole.ALIAS
    assert by_host["staging.loja.test"].status == DomainStatus.ACTIVE


async def test_seeded_staging_host_resolves_platform_tenant(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "platform_tenant_slug", "muhbianco")
    monkeypatch.setattr(settings, "platform_base_domain", "loja.test")
    monkeypatch.setattr(settings, "platform_alias_hosts", "staging.loja.test")
    async with session_factory() as session:
        assert await seed_platform_tenant(session) == 0

    for host in ("loja.test", "staging.loja.test"):
        response = await client.get("/api/v1/storefront/context", headers={"host": host})
        assert response.status_code == 200, response.text
        assert response.json()["tenant"]["slug"] == "muhbianco"
