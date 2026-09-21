"""Locking under real concurrency. MariaDB only (CI sets TEST_DATABASE_URL): SQLite has no row
locks, so there these tests would prove nothing."""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.catalog.schemas import ProductCreate
from app.catalog.service import CatalogService
from app.core.exceptions import ConflictError
from app.inventory.models import InventoryBalance
from app.inventory.schemas import AdjustmentCreate, AdjustmentLine
from app.inventory.service import InventoryService, audit_ledger
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor, TenantService
from tests.conftest import create_tenant

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="needs MariaDB (row locks)"
)

ACTOR = Actor.system("tests")


async def _context(factory: async_sessionmaker[AsyncSession], slug: str) -> TenantContext:
    tenant = await create_tenant(factory, slug)
    async with factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"catalog": True, "inventory": True}, ACTOR
        )
        await session.commit()
    async with factory() as session:
        return await TenantResolver(session).resolve_by_id(tenant.id)


async def _variant(
    factory: async_sessionmaker[AsyncSession], tenant: TenantContext, name: str
) -> str:
    async with factory() as session:
        bind_session_tenant(session, tenant.id)
        view = await CatalogService(session, tenant, ACTOR).create_product(
            ProductCreate(name=name, base_price_cents=100)
        )
        await session.commit()
        return view.variants[0].id


async def _adjust(
    factory: async_sessionmaker[AsyncSession],
    tenant: TenantContext,
    kind: str,
    lines: list[tuple[str, int]],
) -> None:
    async with factory() as session:
        bind_session_tenant(session, tenant.id)
        await InventoryService(session, tenant, ACTOR).adjust(
            AdjustmentCreate(
                kind=kind,  # type: ignore[arg-type]
                reason="teste",
                lines=[AdjustmentLine(variant_id=v, quantity=Decimal(q)) for v, q in lines],
            )
        )
        await session.commit()


async def _on_hand(
    factory: async_sessionmaker[AsyncSession], tenant: TenantContext
) -> dict[str, int]:
    async with factory() as session:
        bind_session_tenant(session, tenant.id)
        rows = (await session.execute(select(InventoryBalance))).scalars()
        return {b.variant_id: b.on_hand_milli for b in rows}


async def test_last_unit_goes_to_exactly_one_of_two_concurrent_losses(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await _context(session_factory, "alpha")
    variant = await _variant(session_factory, tenant, "Brownie")
    await _adjust(session_factory, tenant, "receipt", [(variant, 1)])

    results = await asyncio.gather(
        *(_adjust(session_factory, tenant, "loss", [(variant, 1)]) for _ in range(2)),
        return_exceptions=True,
    )
    assert sum(r is None for r in results) == 1
    assert sum(isinstance(r, ConflictError) for r in results) == 1
    assert (await _on_hand(session_factory, tenant))[variant] == 0
    async with session_factory() as session:
        assert await audit_ledger(session) == 0


async def test_opposite_line_orders_do_not_deadlock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await _context(session_factory, "alpha")
    a = await _variant(session_factory, tenant, "A")
    b = await _variant(session_factory, tenant, "B")
    await _adjust(session_factory, tenant, "receipt", [(a, 10), (b, 10)])

    await asyncio.gather(
        *(
            _adjust(
                session_factory, tenant, "loss", [(a, 1), (b, 1)] if i % 2 else [(b, 1), (a, 1)]
            )
            for i in range(10)
        )
    )
    assert await _on_hand(session_factory, tenant) == {a: 0, b: 0}
    async with session_factory() as session:
        assert await audit_ledger(session) == 0


async def test_concurrent_first_events_of_new_aggregates_do_not_deadlock(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Regression: under REPEATABLE READ, `outbox.emit` locked the gap after the newest
    aggregate (UUIDv7 ids sort last) and concurrent creations deadlocked on it. The app runs
    READ COMMITTED (app.core.database.APP_ISOLATION_LEVEL)."""
    tenants = [await _context(session_factory, f"loja{i}") for i in range(6)]
    await asyncio.gather(*(_variant(session_factory, t, "Produto") for t in tenants))
    for tenant in tenants:
        assert len(await _on_hand(session_factory, tenant)) == 1
