"""Customer orders (stage E, S8): list, cancel, and the payment deadline job."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cart.jobs import purge_carts
from app.cart.models import Cart
from app.inventory.models import InventoryReservation
from app.inventory.service import reserved_mismatches
from app.models.base import utcnow
from app.orders.jobs import run_expire_orders
from app.orders.models import Order
from app.tenancy.context import CROSS_TENANT_OPTION
from tests.shoppers import as_shopper, signed_in
from tests.test_checkout_place import balance, order_body, place, ready_cart, shop  # noqa: F401
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CROSS = {CROSS_TENANT_OPTION: True}
ME = "/api/v1/me/orders"


async def placed_order(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,  # noqa: F811
    units: str = "2",
) -> tuple[dict[str, Any], str]:
    tenant, owner, me = shop
    brownie = await product(client, session_factory, tenant, owner)
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 5)
    cart = await ready_cart(client, me, variant, units)
    response = await place(client, me, order_body(cart))
    assert response.status_code == 201, response.text
    return response.json(), variant


async def test_list_get_and_cancel_before_payment_releases_the_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,  # noqa: F811
) -> None:
    tenant, _, me = shop
    order, variant = await placed_order(client, session_factory, shop)
    listed = (await client.get(ME, headers=me)).json()
    assert [(o["number"], o["status"]) for o in listed["items"]] == [(1, "awaiting_payment")]
    assert (await client.get(f"{ME}/{order['id']}", headers=me)).json()["total_cents"] == 3000

    someone = as_shopper(tenant, await signed_in(session_factory, tenant))
    assert (await client.get(ME, headers=someone)).json()["items"] == []
    stolen = await client.post(f"{ME}/{order['id']}/cancel", json={}, headers=someone)
    assert stolen.status_code == 404

    cancelled = await client.post(
        f"{ME}/{order['id']}/cancel", json={"reason": "desisti"}, headers=me
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    assert [e["status"] for e in cancelled.json()["timeline"]] == ["awaiting_payment", "cancelled"]
    assert await balance(session_factory, variant) == (5000, 0)  # held stock is back
    again = await client.post(f"{ME}/{order['id']}/cancel", json={}, headers=me)
    assert again.status_code == 409 and again.json()["error"]["code"] == "cancel_window_closed"

    # Order history stays visible when the store stops selling online.
    async with session_factory() as session:
        from app.tenancy.service import Actor, TenantService

        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"checkout": False}, Actor.system("t")
        )
        await session.commit()
    assert len((await client.get(ME, headers=me)).json()["items"]) == 1


async def test_the_deadline_job_fails_unpaid_orders_once(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,  # noqa: F811
) -> None:
    order, variant = await placed_order(client, session_factory, shop)
    assert await run_expire_orders(session_factory, utcnow()) == 0  # not due yet
    async with session_factory() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order["id"])
            .values(expires_at=utcnow() - timedelta(minutes=1))
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    assert await run_expire_orders(session_factory, utcnow()) == 1
    assert await run_expire_orders(session_factory, utcnow()) == 0  # second run: nothing
    _, _, me = shop
    failed = (await client.get(f"{ME}/{order['id']}", headers=me)).json()
    assert failed["status"] == "failed"
    assert await balance(session_factory, variant) == (5000, 0)
    async with session_factory() as session:
        statuses = (
            await session.execute(
                select(InventoryReservation.status, InventoryReservation.release_reason)
                .where(InventoryReservation.order_id == order["id"])
                .execution_options(**CROSS)
            )
        ).all()
        assert [tuple(row) for row in statuses] == [("expired", "expired")]
        assert await reserved_mismatches(session) == 0


async def test_old_converted_carts_are_purged(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,  # noqa: F811
) -> None:
    await placed_order(client, session_factory, shop)
    async with session_factory() as session:
        assert await purge_carts(session) == 0
        await session.execute(
            update(Cart)
            .values(last_activity_at=utcnow() - timedelta(days=91))
            .execution_options(synchronize_session=False, **CROSS)
        )
        assert await purge_carts(session) == 1
        await session.commit()


async def test_cancelling_a_paid_order_returns_the_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: Any,  # noqa: F811
) -> None:
    from app.inventory.models import InventoryMovement
    from tests.test_catalog import add_ready_image, base
    from tests.test_events import event_body

    tenant, owner, me = shop
    talk = (
        await client.post(
            f"{base(tenant)}/products",
            json={"name": "Palestra", "kind": "ticket", "base_price_cents": 0},
            headers=owner,
        )
    ).json()
    url = f"{base(tenant)}/products/{talk['id']}"
    await client.put(f"{url}/event", json=event_body(), headers=owner)
    lot = (
        await client.post(
            f"{url}/event/lots",
            json={"name": "Grátis", "price_cents": 0, "quantity": 10},
            headers=owner,
        )
    ).json()["lots"][0]
    await add_ready_image(session_factory, tenant, talk["id"])
    await client.post(f"{url}/publish", headers=owner)
    cart = await ready_cart(client, me, lot["variant_id"], "3")
    order = (await place(client, me, order_body(cart))).json()
    assert order["status"] == "payment_confirmed"
    assert await balance(session_factory, lot["variant_id"]) == (7000, 0)

    cancelled = await client.post(f"{ME}/{order['id']}/cancel", json={}, headers=me)
    assert cancelled.json()["status"] == "cancelled"
    assert await balance(session_factory, lot["variant_id"]) == (10000, 0)
    async with session_factory() as session:
        kinds = (
            await session.execute(
                select(InventoryMovement.movement_type, InventoryMovement.qty_milli)
                .where(InventoryMovement.reference_id == order["id"])
                .order_by(InventoryMovement.id)
                .execution_options(**CROSS)
            )
        ).all()
    assert [tuple(k) for k in kinds] == [("sale_commit", -3000), ("sale_return", 3000)]
