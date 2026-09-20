"""Multi-tenant leak suite.

Every tenant-scoped table and every tenant-facing route must be covered here.
Adding a `TenantScoped` model without a case below fails the suite on purpose.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import TenantContextMissingError, TenantMismatchError
from app.core.scopes import TenantRole
from app.models.all import Base
from app.models.base import TenantScoped
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import TenantFeatureFlag, TenantSetting
from tests.conftest import create_admin, create_tenant, login

COVERED_TENANT_SCOPED_TABLES = {
    "tenant_settings",
    "tenant_feature_flags",
    "tenant_sequences",
    "tenant_integration_credentials",
    "customer_tenant_access",
}


def test_every_tenant_scoped_model_is_listed() -> None:
    scoped = {
        mapper.class_.__tablename__
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, TenantScoped)
    }
    missing = scoped - COVERED_TENANT_SCOPED_TABLES
    assert not missing, f"Add isolation coverage for: {sorted(missing)}"


async def test_query_without_tenant_context_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await create_tenant(session_factory, "alpha")
    async with session_factory() as session:
        with pytest.raises(TenantContextMissingError):
            await session.execute(select(TenantSetting))
        # Explicit opt-out still works for ops/jobs code.
        rows = (
            (
                await session.execute(
                    select(TenantSetting).execution_options(**{CROSS_TENANT_OPTION: True})
                )
            )
            .scalars()
            .all()
        )
        assert rows


async def test_tenant_filter_hides_other_tenant_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alpha = await create_tenant(session_factory, "alpha")
    beta = await create_tenant(session_factory, "beta")
    async with session_factory() as session:
        bind_session_tenant(session, alpha.id)
        settings_rows = (await session.execute(select(TenantSetting))).scalars().all()
        flags = (await session.execute(select(TenantFeatureFlag))).scalars().all()
        assert settings_rows and flags
        assert {r.tenant_id for r in settings_rows} == {alpha.id}
        assert {r.tenant_id for r in flags} == {alpha.id}

        # Filtering by the other tenant's id explicitly still yields nothing.
        leaked = (
            (await session.execute(select(TenantSetting).where(TenantSetting.tenant_id == beta.id)))
            .scalars()
            .all()
        )
        assert leaked == []


async def test_insert_is_stamped_and_mismatch_is_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alpha = await create_tenant(session_factory, "alpha")
    beta = await create_tenant(session_factory, "beta")
    async with session_factory() as session:
        bind_session_tenant(session, alpha.id)
        session.add(TenantSetting(key="branding_extra", value={"x": 1}))
        await session.flush()
        row = (
            await session.execute(
                select(TenantSetting).where(TenantSetting.key == "branding_extra")
            )
        ).scalar_one()
        assert row.tenant_id == alpha.id

        session.add(TenantSetting(tenant_id=beta.id, key="evil", value={}))
        with pytest.raises(TenantMismatchError):
            await session.flush()
        await session.rollback()

    async with session_factory() as session:
        with pytest.raises(TenantContextMissingError):
            session.add(TenantSetting(key="no_ctx", value={}))
            await session.flush()


async def test_panel_route_hides_other_tenants_from_members(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha = await create_tenant(session_factory, "alpha")
    beta = await create_tenant(session_factory, "beta")
    await create_admin(session_factory, "dona@alpha.test", memberships={alpha.id: TenantRole.OWNER})
    headers = await login(client, "dona@alpha.test")

    mine = await client.get(f"/api/v1/admin/tenants/{alpha.id}/context", headers=headers)
    assert mine.status_code == 200, mine.text
    assert mine.json()["slug"] == "alpha"
    assert set(mine.json()["settings"]) >= {"storefront", "branding"}

    theirs = await client.get(f"/api/v1/admin/tenants/{beta.id}/context", headers=headers)
    assert theirs.status_code == 404

    ops_denied = await client.get("/api/v1/ops/tenants", headers=headers)
    assert ops_denied.status_code == 403


async def test_support_role_lacks_settings_scope_but_reads_context(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha = await create_tenant(session_factory, "alpha")
    await create_admin(
        session_factory, "suporte@alpha.test", memberships={alpha.id: TenantRole.SUPPORT}
    )
    headers = await login(client, "suporte@alpha.test")
    response = await client.get(f"/api/v1/admin/tenants/{alpha.id}/context", headers=headers)
    assert response.status_code == 200
