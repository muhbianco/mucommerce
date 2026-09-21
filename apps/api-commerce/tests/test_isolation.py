"""Multi-tenant leak suite.

Every tenant-scoped table and every tenant-facing route must be covered here.
Adding a `TenantScoped` model without a case below fails the suite on purpose.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    CATALOG_ACCESS_GUARD_ATTR,
    CUSTOMER_GUARD_ATTR,
    PLATFORM_GUARD_ATTR,
    TENANT_GUARD_ATTR,
)
from app.api.v1.router import ENDPOINT_ROUTERS
from app.core.exceptions import TenantContextMissingError, TenantMismatchError
from app.core.scopes import TenantRole
from app.models.all import Base
from app.models.base import TenantScoped
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import TenantFeatureFlag, TenantSetting
from tests.conftest import create_admin, create_tenant, login

COVERED_TENANT_SCOPED_TABLES = {
    "categories",
    "inventory_balances",
    "inventory_movements",
    "stock_adjustments",
    "media_assets",
    "products",
    "product_variants",
    "product_categories",
    "tenant_settings",
    "tenant_feature_flags",
    "tenant_sequences",
    "tenant_integration_credentials",
    "customer_tenant_access",
    "customer_sessions",
    "legal_documents",
    "consents",
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


TENANT_SCOPED_MODELS = sorted(
    (m.class_ for m in Base.registry.mappers if issubclass(m.class_, TenantScoped)),
    key=lambda cls: cls.__tablename__,
)
V1_ROUTES = [route for r in ENDPOINT_ROUTERS for route in r.routes if isinstance(route, APIRoute)]


@pytest.mark.parametrize("model", TENANT_SCOPED_MODELS, ids=lambda cls: cls.__tablename__)
async def test_every_tenant_scoped_statement_carries_the_tenant_predicate(
    session_factory: async_sessionmaker[AsyncSession], model: Any
) -> None:
    """Generic: SELECT and bulk UPDATE/DELETE on every TenantScoped model get `tenant_id = ?`."""
    statements: list[str] = []

    def capture(*args: Any) -> None:
        statements.append(args[2])  # (conn, cursor, statement, parameters, context, executemany)

    async with session_factory() as session:
        engine = session.bind.sync_engine  # type: ignore[union-attr]
        event.listen(engine, "before_cursor_execute", capture)
        try:
            bind_session_tenant(session, "0192a1b2-0000-7000-8000-000000000001")
            no_sync = {"synchronize_session": False}
            await session.execute(select(model))
            await session.execute(
                update(model).values(tenant_id=model.tenant_id).execution_options(**no_sync)
            )
            await session.execute(delete(model).execution_options(**no_sync))
            await session.rollback()
        finally:
            event.remove(engine, "before_cursor_execute", capture)

    table = model.__tablename__
    assert len(statements) == 3
    for sql in statements:
        assert f"{table}.tenant_id = " in sql, sql


async def test_bulk_write_only_touches_the_bound_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alpha = await create_tenant(session_factory, "alpha")
    beta = await create_tenant(session_factory, "beta")
    async with session_factory() as session:
        bind_session_tenant(session, alpha.id)
        await session.execute(
            update(TenantSetting)
            .values(schema_version=7)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(TenantSetting.tenant_id, TenantSetting.schema_version).execution_options(
                    **{CROSS_TENANT_OPTION: True}
                )
            )
        ).all()
    assert {version for tenant_id, version in rows if tenant_id == alpha.id} == {7}
    assert 7 not in {version for tenant_id, version in rows if tenant_id == beta.id}

    async with session_factory() as session:
        with pytest.raises(TenantContextMissingError):
            await session.execute(delete(TenantSetting))


def _guards(route: APIRoute) -> set[str]:
    found: set[str] = set()
    pending = list(route.dependant.dependencies)
    while pending:
        dependency = pending.pop()
        for attr in (
            PLATFORM_GUARD_ATTR,
            TENANT_GUARD_ATTR,
            CATALOG_ACCESS_GUARD_ATTR,
            CUSTOMER_GUARD_ATTR,
        ):
            if getattr(dependency.call, attr, False):
                found.add(attr)
        pending.extend(dependency.dependencies)
    return found


def test_every_tenant_panel_route_requires_membership() -> None:
    routes = [r for r in V1_ROUTES if r.path.startswith("/admin/tenants/{tenant_id}")]
    assert routes
    unguarded = [
        f"{sorted(r.methods)} {r.path}" for r in routes if TENANT_GUARD_ATTR not in _guards(r)
    ]
    assert not unguarded, unguarded


def test_every_storefront_catalog_route_checks_catalog_access() -> None:
    routes = [r for r in V1_ROUTES if r.path.startswith("/storefront/catalog")]
    assert routes
    unguarded = [
        f"{sorted(r.methods)} {r.path}"
        for r in routes
        if CATALOG_ACCESS_GUARD_ATTR not in _guards(r)
    ]
    assert not unguarded, unguarded


def test_every_customer_route_requires_a_session_of_the_store() -> None:
    routes = [r for r in V1_ROUTES if r.path.startswith("/me/")]
    assert routes
    unguarded = [
        f"{sorted(r.methods)} {r.path}" for r in routes if CUSTOMER_GUARD_ATTR not in _guards(r)
    ]
    assert not unguarded, unguarded


def test_every_ops_route_requires_a_platform_role() -> None:
    routes = [r for r in V1_ROUTES if r.path.startswith("/ops/")]
    assert routes
    unguarded = [
        f"{sorted(r.methods)} {r.path}" for r in routes if PLATFORM_GUARD_ATTR not in _guards(r)
    ]
    assert not unguarded, unguarded


async def test_catalog_routes_never_reach_another_tenants_rows(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Ids from tenant B used under tenant A's path answer 404/422, never B's data."""
    from app.tenancy.service import Actor, TenantService

    tenants = {}
    headers = {}
    for slug in ("alpha", "beta"):
        tenant = await create_tenant(session_factory, slug)
        async with session_factory() as session:
            service = TenantService(session)
            await service.set_features(
                await service.get_or_404(tenant.id), {"catalog": True}, Actor.system("tests")
            )
            await session.commit()
        await create_admin(
            session_factory, f"dona@{slug}.test", memberships={tenant.id: TenantRole.OWNER}
        )
        tenants[slug] = tenant
        headers[slug] = await login(client, f"dona@{slug}.test")

    alpha_base = f"/api/v1/admin/tenants/{tenants['alpha'].id}"
    beta_base = f"/api/v1/admin/tenants/{tenants['beta'].id}"
    beta_product = (
        await client.post(
            f"{beta_base}/products",
            json={"name": "Segredo", "base_price_cents": 100},
            headers=headers["beta"],
        )
    ).json()
    beta_category = (
        await client.post(f"{beta_base}/categories", json={"name": "Dela"}, headers=headers["beta"])
    ).json()
    variant_id = beta_product["variants"][0]["id"]
    mine = headers["alpha"]

    product_url = f"{alpha_base}/products/{beta_product['id']}"
    assert (await client.get(product_url, headers=mine)).status_code == 404
    assert (await client.patch(product_url, json={"name": "x"}, headers=mine)).status_code == 404
    assert (await client.delete(product_url, headers=mine)).status_code == 404
    assert (await client.post(f"{product_url}/publish", headers=mine)).status_code == 404
    variant_url = f"{product_url}/variants/{variant_id}"
    assert (await client.patch(variant_url, json={"name": "x"}, headers=mine)).status_code == 404
    category_url = f"{alpha_base}/categories/{beta_category['id']}"
    assert (await client.patch(category_url, json={"name": "x"}, headers=mine)).status_code == 404
    assert (await client.delete(category_url, headers=mine)).status_code == 404

    linked = await client.post(
        f"{alpha_base}/products",
        json={"name": "Meu", "base_price_cents": 100, "category_ids": [beta_category["id"]]},
        headers=mine,
    )
    assert linked.status_code == 422
    child = await client.post(
        f"{alpha_base}/categories",
        json={"name": "Filha", "parent_id": beta_category["id"]},
        headers=mine,
    )
    assert child.status_code == 422
    listed = await client.get(f"{alpha_base}/products", headers=mine)
    assert listed.json()["items"] == []
    # Tenant B's path with tenant A's credentials: the tenant itself does not exist for A.
    assert (await client.get(f"{beta_base}/products", headers=mine)).status_code == 404
    # Same SKU sequence number in both tenants: numbering is per tenant.
    mine_product = (
        await client.post(
            f"{alpha_base}/products", json={"name": "Meu", "base_price_cents": 1}, headers=mine
        )
    ).json()
    assert mine_product["sku"] == beta_product["sku"] == "P00001"
