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


# ----------------------------------------------------------------------------- checkout (stage E)
async def _selling_context(factory: async_sessionmaker[AsyncSession], slug: str) -> TenantContext:
    tenant = await create_tenant(factory, slug)
    async with factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        await service.set_features(
            row,
            {"catalog": True, "inventory": True, "checkout": True, "pickup": True},
            ACTOR,
        )
        await service.set_setting(
            row,
            "fulfillment",
            {"pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A"}]}},
            ACTOR,
        )
        await service.set_setting(row, "checkout", {"max_open_orders": 10}, ACTOR)
        await session.commit()
    async with factory() as session:
        return await TenantResolver(session).resolve_by_id(tenant.id)


async def _published(
    factory: async_sessionmaker[AsyncSession], tenant: TenantContext, name: str, units: int
) -> str:
    from app.media.models import MediaAsset, MediaStatus

    async with factory() as session:
        bind_session_tenant(session, tenant.id)
        catalog = CatalogService(session, tenant, ACTOR)
        view = await catalog.create_product(ProductCreate(name=name, base_price_cents=100))
        session.add(
            MediaAsset(
                owner_type="product",
                owner_id=view.product.id,
                status=MediaStatus.READY,
                declared_mime="image/png",
                declared_bytes=1,
                upload_key="incoming/x",
                renditions={"orig": {"key": "k", "width": 1, "height": 1}},
            )
        )
        await session.flush()
        await catalog.publish(view.product.id)
        await session.commit()
        variant_id = view.variants[0].id
    await _adjust(factory, tenant, "receipt", [(variant_id, units)])
    return variant_id


async def _cart(
    factory: async_sessionmaker[AsyncSession], tenant: TenantContext, lines: list[str]
) -> tuple[str, int, int, str]:
    """A new customer with a cart of `lines` (one unit each) and pickup chosen."""
    from app.cart.service import CartService
    from app.core.ids import new_id
    from app.identity.models import Customer
    from app.models.base import utcnow

    customer_id = new_id()
    async with factory() as session:
        session.add(Customer(id=customer_id, email_normalized=f"{customer_id}@t.test"))
        await session.flush()
        bind_session_tenant(session, tenant.id)
        carts = CartService(session, tenant, customer_id, utcnow())
        for variant_id in lines:
            await carts.add(variant_id, 1000, [])
        location = tenant.settings["fulfillment"]["pickup"]["locations"][0]["id"]
        view = await carts.set_fulfillment({"type": "pickup", "pickup_location_id": location})
        await session.commit()
        assert view.cart is not None
        return customer_id, view.cart.version, view.quote.total_cents, view.cart.id


async def _place(
    factory: async_sessionmaker[AsyncSession],
    tenant: TenantContext,
    cart: tuple[str, int, int, str],
) -> None:
    from app.orders.commands import CartSource, Contact, PlaceOrder
    from app.orders.service import OrderService

    customer_id, version, total, cart_id = cart
    async with factory() as session:
        bind_session_tenant(session, tenant.id)
        await OrderService(session, tenant, ACTOR).place(
            PlaceOrder(
                origin="storefront",
                customer_id=customer_id,
                idempotency_key=cart_id,
                source=CartSource(cart_id, version),
                contact=Contact("Cliente"),
                expected_total_cents=total,
            )
        )
        await session.commit()


async def test_twenty_checkouts_for_five_units_make_exactly_five_orders(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    from app.core.exceptions import CartProblemsError

    tenant = await _selling_context(session_factory, "alpha")
    variant = await _published(session_factory, tenant, "Brownie", 5)
    carts = [await _cart(session_factory, tenant, [variant]) for _ in range(20)]

    results = await asyncio.gather(
        *(_place(session_factory, tenant, cart) for cart in carts), return_exceptions=True
    )
    assert sum(r is None for r in results) == 5, [r for r in results if r is not None][:3]
    assert all(r is None or isinstance(r, CartProblemsError) for r in results)
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        row = (
            await session.execute(
                select(InventoryBalance).where(InventoryBalance.variant_id == variant)
            )
        ).scalar_one()
        assert (row.on_hand_milli, row.reserved_milli) == (5000, 5000)


async def test_carts_in_opposite_order_do_not_deadlock_on_place(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await _selling_context(session_factory, "alpha")
    a = await _published(session_factory, tenant, "A", 20)
    b = await _published(session_factory, tenant, "B", 20)
    carts = [await _cart(session_factory, tenant, [a, b] if i % 2 else [b, a]) for i in range(10)]
    await asyncio.gather(*(_place(session_factory, tenant, cart) for cart in carts))
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        rows = (await session.execute(select(InventoryBalance))).scalars()
        assert {r.variant_id: r.reserved_milli for r in rows} == {a: 10000, b: 10000}
