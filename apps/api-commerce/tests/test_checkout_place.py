"""Checkout (stage E, S7): OrderService.place through POST /checkout/orders."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.inventory.models import InventoryBalance, InventoryMovement, InventoryReservation
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_cart import FULFILLMENT
from tests.test_catalog import base, member_headers
from tests.test_events import event_body
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CROSS = {CROSS_TENANT_OPTION: True}
CART = "/api/v1/cart"
ORDERS = "/api/v1/checkout/orders"


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str], dict[str, str]]:
    tenant = await selling_store(
        session_factory,
        flags={"delivery": True, "events": True},
        settings={"fulfillment": FULFILLMENT},
    )
    owner = await member_headers(client, session_factory, tenant)
    me = as_shopper(tenant, await signed_in(session_factory, tenant))
    return tenant, owner, me


async def ready_cart(
    client: AsyncClient, me: dict[str, str], variant_id: str, quantity: str = "1"
) -> dict[str, Any]:
    await client.post(
        f"{CART}/items", json={"variant_id": variant_id, "quantity": quantity}, headers=me
    )
    cart = (await client.get(CART, headers=me)).json()
    if cart["quote"]["needs_fulfillment"]:
        location = cart["options"]["pickup_locations"][0]["id"]
        cart = (
            await client.put(
                f"{CART}/fulfillment",
                json={"type": "pickup", "pickup_location_id": location},
                headers=me,
            )
        ).json()
    return dict(cart)


def order_body(cart: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    return {
        "cart_id": cart["id"],
        "cart_version": cart["version"],
        "expected_total_cents": cart["quote"]["total_cents"],
        "contact": {"name": "Maria"},
    } | overrides


async def place(
    client: AsyncClient, me: dict[str, str], body: dict[str, Any], key: str | None = None
) -> Any:
    headers = me | {"Idempotency-Key": key or str(uuid.uuid4())}
    return await client.post(ORDERS, json=body, headers=headers)


async def balance(
    session_factory: async_sessionmaker[AsyncSession], variant_id: str
) -> tuple[int, int]:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(InventoryBalance.on_hand_milli, InventoryBalance.reserved_milli)
                .where(InventoryBalance.variant_id == variant_id)
                .execution_options(**CROSS)
            )
        ).one()
    return int(row[0]), int(row[1])


async def test_place_reserves_stock_snapshots_lines_and_replays_the_key(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    brownie = await product(client, session_factory, tenant, owner)
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 5)
    cart = await ready_cart(client, me, variant, "2")

    key = str(uuid.uuid4())
    placed = await place(client, me, order_body(cart), key)
    assert placed.status_code == 201, placed.text
    order = placed.json()
    assert (order["number"], order["status"], order["total_cents"]) == (1, "awaiting_payment", 3000)
    assert order["expires_at"] and order["fulfillment"]["name"] == "Loja"
    [item] = order["items"]
    assert (item["name"], item["sku"], item["quantity"], item["unit_price_cents"]) == (
        "Brownie",
        brownie["sku"],
        "2",
        1500,
    )
    assert await balance(session_factory, variant) == (5000, 2000)  # reserved, not sold

    again = await place(client, me, order_body(cart), key)
    assert again.json()["id"] == order["id"]
    assert again.headers.get("Idempotent-Replayed") == "true"
    other_body = await place(client, me, order_body(cart, notes="x"), key)
    assert other_body.status_code == 422
    assert other_body.json()["error"]["code"] == "idempotency_key_reused"

    converted = await place(client, me, order_body(cart))
    assert converted.json()["error"]["code"] == "cart_already_converted"
    assert (await client.get(CART, headers=me)).json()["id"] is None  # a new cart starts empty

    someone = as_shopper(tenant, await signed_in(session_factory, tenant))
    assert (await client.get(f"{ORDERS}/{order['id']}", headers=someone)).status_code == 404
    mine = await client.get(f"{ORDERS}/{order['id']}", headers=me)
    assert [event["status"] for event in mine.json()["timeline"]] == ["awaiting_payment"]

    # Stock held by the order cannot be taken by a manual loss.
    loss = await client.post(
        f"{base(tenant)}/inventory/adjustments",
        json={
            "kind": "loss",
            "reason": "quebra",
            "lines": [{"variant_id": variant, "quantity": "4"}],
        },
        headers=owner | {"Idempotency-Key": str(uuid.uuid4())},
    )
    assert loss.status_code == 409 and loss.json()["error"]["code"] == "reserved_stock"


async def test_what_the_customer_saw_must_still_hold(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    brownie = await product(client, session_factory, tenant, owner)
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 3)
    cart = await ready_cart(client, me, variant, "2")

    stale = await place(client, me, order_body(cart, cart_version=cart["version"] - 1))
    assert stale.json()["error"]["code"] == "cart_changed"
    wrong_total = await place(client, me, order_body(cart, expected_total_cents=1))
    assert wrong_total.json()["error"]["code"] == "cart_changed"
    assert wrong_total.json()["error"]["details"]["total_cents"] == 3000

    await set_stock(session_factory, variant, 1)  # sold elsewhere meanwhile
    gone = await place(client, me, order_body(cart))
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "cart_problems"
    assert gone.json()["error"]["details"]["lines"][0]["code"] == "out_of_stock"
    assert await balance(session_factory, variant) == (1000, 0)  # nothing reserved


async def test_terms_delivery_and_open_orders_rules(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    cake = await product(client, session_factory, tenant, owner, stock_policy="unlimited")
    variant = cake["variants"][0]["id"]
    await client.post(f"{CART}/items", json={"variant_id": variant, "quantity": "1"}, headers=me)
    no_choice = (await client.get(CART, headers=me)).json()
    missing = await place(client, me, order_body(no_choice))
    assert missing.json()["error"]["code"] == "fulfillment_invalid"

    published = await client.post(
        f"{base(tenant)}/legal-documents",
        json={"kind": "terms", "content": "Termos de uso da loja para compras online."},
        headers=owner,
    )
    assert published.status_code in (200, 201), published.text
    cart = await ready_cart(client, me, variant)
    refused = await place(client, me, order_body(cart))
    assert refused.status_code == 422
    assert refused.json()["error"]["details"]["versions"] == {"terms": 1}
    accepted = await place(client, me, order_body(cart, consent={"terms_version": 1}))
    assert accepted.status_code == 201, accepted.text

    await client.put(
        f"{base(tenant)}/settings/checkout", json={"value": {"max_open_orders": 1}}, headers=owner
    )
    second = await ready_cart(client, me, variant)
    capped = await place(client, me, order_body(second, consent={"terms_version": 1}))
    assert capped.json()["error"]["code"] == "too_many_open_orders"


async def test_a_free_ticket_is_paid_at_once_and_leaves_the_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    show = (
        await client.post(
            f"{base(tenant)}/products",
            json={"name": "Palestra", "kind": "ticket", "base_price_cents": 0},
            headers=owner,
        )
    ).json()
    url = f"{base(tenant)}/products/{show['id']}"
    await client.put(f"{url}/event", json=event_body(), headers=owner)
    lot = (
        await client.post(
            f"{url}/event/lots",
            json={"name": "Grátis", "price_cents": 0, "quantity": 10},
            headers=owner,
        )
    ).json()["lots"][0]
    from tests.test_catalog import add_ready_image

    await add_ready_image(session_factory, tenant, show["id"])
    await client.post(f"{url}/publish", headers=owner)

    cart = await ready_cart(client, me, lot["variant_id"], "2")
    assert cart["quote"]["fulfillment"]["type"] == "none"
    placed = await place(client, me, order_body(cart))
    assert placed.status_code == 201, placed.text
    order = placed.json()
    assert (order["status"], order["total_cents"], order["expires_at"]) == (
        "payment_confirmed",
        0,
        None,
    )
    assert order["items"][0]["event"]["lot_name"] == "Grátis"
    assert await balance(session_factory, lot["variant_id"]) == (8000, 0)
    async with session_factory() as session:
        committed = (
            await session.execute(
                select(InventoryReservation.status, InventoryMovement.movement_type)
                .join(
                    InventoryMovement,
                    InventoryMovement.reference_id == InventoryReservation.order_id,
                )
                .where(InventoryReservation.order_id == order["id"])
                .execution_options(**CROSS)
            )
        ).all()
    assert [tuple(row) for row in committed] == [("committed", "sale_commit")]
