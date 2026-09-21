from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cli import disable_domain, seed_platform_tenant
from app.core.config import settings
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import (
    DomainKind,
    DomainPurpose,
    DomainRole,
    DomainStatus,
    TenantDomain,
)


@pytest.fixture(autouse=True)
def _platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "platform_tenant_slug", "muhbianco")
    monkeypatch.setattr(settings, "platform_base_domain", "loja.test")


async def _domains(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, TenantDomain]:
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
    return {row.hostname: row for row in rows}


async def test_seed_serves_the_platform_tenant_only_at_the_base_domain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    for _ in range(2):  # idempotent
        async with session_factory() as session:
            assert await seed_platform_tenant(session) == 0

    by_host = await _domains(session_factory)
    assert set(by_host) == {"loja.test", "muhbianco.loja.test"}
    assert by_host["loja.test"].role == DomainRole.PRIMARY
    assert by_host["loja.test"].status == DomainStatus.ACTIVE
    assert by_host["muhbianco.loja.test"].status == DomainStatus.DISABLED


async def test_seeded_platform_tenant_resolves_only_at_the_base_domain(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await seed_platform_tenant(session) == 0

    response = await client.get("/api/v1/storefront/context", headers={"host": "loja.test"})
    assert response.status_code == 200, response.text
    assert response.json()["tenant"]["slug"] == "muhbianco"
    for host in ("muhbianco.loja.test", "staging.loja.test"):
        response = await client.get("/api/v1/storefront/context", headers={"host": host})
        assert response.status_code == 404, host


async def test_disable_domain_cli(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        assert await seed_platform_tenant(session) == 0
    by_host = await _domains(session_factory)
    async with session_factory() as session:
        session.add(
            TenantDomain(
                tenant_id=by_host["loja.test"].tenant_id,
                hostname="staging.loja.test",
                kind=DomainKind.CUSTOM_SUBDOMAIN,
                purpose=DomainPurpose.STOREFRONT,
                role=DomainRole.ALIAS,
                status=DomainStatus.ACTIVE,
                verified_at=utcnow(),
            )
        )
        await session.commit()

    for _ in range(2):  # second run is a no-op
        async with session_factory() as session:
            assert await disable_domain("Staging.Loja.Test.", session) == 0
    assert (await _domains(session_factory))["staging.loja.test"].status == DomainStatus.DISABLED

    async with session_factory() as session:
        assert await disable_domain("nope.loja.test", session) == 1
    async with session_factory() as session:
        assert await disable_domain("loja.test", session) == 3  # primary stays up
    async with session_factory() as session:
        assert await disable_domain("bad_host", session) == 2
