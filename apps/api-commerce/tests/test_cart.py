"""Cart (stage E, S5): priced on every read, validated on add, one active cart per customer."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cart.models import Cart
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.shoppers import as_shopper, selling_store, signed_in
from tests.test_catalog import base, member_headers
from tests.test_pricing import product
from tests.test_storefront_catalog import set_stock

CART = "/api/v1/cart"
CROSS = {CROSS_TENANT_OPTION: True}
FULFILLMENT = {
    "pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A, 1"}]},
    "delivery": {
        "enabled": True,
        "zones": [
            {
                "name": "Centro",
                "kind": "cep_ranges",
                "cep_ranges": [{"start": "01000000", "end": "01999999"}],
                "fee_cents": 800,
            }
        ],
    },
}


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str], dict[str, str]]:
    tenant = await selling_store(
        session_factory, flags={"delivery": True}, settings={"fulfillment": FULFILLMENT}
    )
    owner = await member_headers(client, session_factory, tenant)
    shopper = as_shopper(tenant, await signed_in(session_factory, tenant))
    return tenant, owner, shopper


async def add(client: AsyncClient, headers: dict[str, str], **body: Any) -> Any:
    return await client.post(f"{CART}/items", json=body, headers=headers)


async def test_add_merges_lines_prices_on_read_and_checks_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    brownie = await product(client, session_factory, tenant, owner)
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 3)

    empty = (await client.get(CART, headers=me)).json()
    assert (empty["id"], empty["items"], empty["quote"]["can_checkout"]) == (None, [], False)

    first = await add(client, me, variant_id=variant, quantity="2")
    assert first.status_code == 201, first.text
    again = (await add(client, me, variant_id=variant, quantity="1")).json()
    [line] = again["items"]
    assert (line["quantity"], line["unit_price_cents"], line["subtotal_cents"]) == ("3", 1500, 4500)
    assert line["name"] == "Brownie" and line["problem"] is None
    assert again["version"] == first.json()["version"] + 1

    too_many = await add(client, me, variant_id=variant, quantity="1")
    assert too_many.status_code == 409
    assert too_many.json()["error"]["code"] == "out_of_stock"
    assert too_many.json()["error"]["details"]["available_milli"] == 3000

    # The price is read again: a promotion shows up at once, nothing is stored.
    await client.patch(
        f"{base(tenant)}/products/{brownie['id']}",
        json={"promo_price_cents": 1000},
        headers=owner,
    )
    repriced = (await client.get(CART, headers=me)).json()
    assert repriced["items"][0]["unit_price_cents"] == 1000
    assert repriced["quote"]["subtotal_cents"] == 3000

    # Paused later: the line stays, with its problem, and checkout is blocked.
    await client.post(f"{base(tenant)}/products/{brownie['id']}/pause", headers=owner)
    paused = (await client.get(CART, headers=me)).json()
    assert paused["items"][0]["problem"]["code"] == "unavailable"
    assert paused["items"][0]["subtotal_cents"] is None
    assert paused["quote"]["problems"] == 1 and paused["quote"]["can_checkout"] is False

    item_id = paused["items"][0]["id"]
    removed = await client.delete(f"{CART}/items/{item_id}", headers=me)
    assert removed.json()["items"] == []


async def test_quantity_changes_and_invalid_adds(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    cake = await product(client, session_factory, tenant, owner, stock_policy="unlimited")
    variant = cake["variants"][0]["id"]
    added = (await add(client, me, variant_id=variant, quantity="1")).json()
    item = added["items"][0]["id"]

    changed = await client.patch(f"{CART}/items/{item}", json={"quantity": "4"}, headers=me)
    assert changed.json()["items"][0]["quantity"] == "4"
    half = await client.patch(f"{CART}/items/{item}", json={"quantity": "1.5"}, headers=me)
    assert half.json()["error"]["code"] == "invalid_quantity"  # sold by unit
    zero = await client.patch(f"{CART}/items/{item}", json={"quantity": "0"}, headers=me)
    assert zero.json()["items"] == []

    unknown = await add(client, me, variant_id="0" * 36, quantity="1")
    assert unknown.json()["error"]["code"] == "item_unavailable"
    for bad in (
        {"variant_id": variant, "quantity": "0"},
        {"variant_id": variant, "quantity": "1000"},
    ):
        assert (await add(client, me, **bad)).status_code == 422
    other = await client.patch(f"{CART}/items/{'0' * 36}", json={"quantity": "1"}, headers=me)
    assert other.status_code == 404


async def test_fulfillment_choice_prices_delivery_and_needs_an_owned_address(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    cake = await product(client, session_factory, tenant, owner, stock_policy="unlimited")
    await add(client, me, variant_id=cake["variants"][0]["id"], quantity="2")
    cart = (await client.get(CART, headers=me)).json()
    assert cart["quote"]["needs_fulfillment"] is True and cart["quote"]["fulfillment"] is None
    assert cart["options"]["modes"] == ["pickup", "delivery"]
    location_id = cart["options"]["pickup_locations"][0]["id"]

    pickup = await client.put(
        f"{CART}/fulfillment",
        json={"type": "pickup", "pickup_location_id": location_id},
        headers=me,
    )
    assert pickup.json()["quote"]["can_checkout"] is True
    assert pickup.json()["quote"]["total_cents"] == 3000

    address = (
        await client.post(
            "/api/v1/me/addresses",
            json={
                "recipient_name": "Maria",
                "postal_code": "01310-100",
                "street": "Av. Paulista",
                "number": "1000",
                "district": "Bela Vista",
                "city": "São Paulo",
                "state": "SP",
            },
            headers=me,
        )
    ).json()
    delivery = (
        await client.put(
            f"{CART}/fulfillment",
            json={"type": "delivery", "address_id": address["id"]},
            headers=me,
        )
    ).json()
    assert delivery["quote"]["delivery_fee_cents"] == 800
    assert delivery["quote"]["total_cents"] == 3800
    assert delivery["quote"]["fulfillment"]["snapshot"]["name"] == "Centro"

    someone = as_shopper(tenant, await signed_in(session_factory, tenant))
    stolen = await client.put(
        f"{CART}/fulfillment",
        json={"type": "delivery", "address_id": address["id"]},
        headers=someone,
    )
    assert stolen.status_code == 404


async def test_one_active_cart_per_customer_and_carts_are_private(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, owner, me = shop
    cake = await product(client, session_factory, tenant, owner, stock_policy="unlimited")
    variant = cake["variants"][0]["id"]
    for _ in range(3):
        await add(client, me, variant_id=variant, quantity="1")
    someone = as_shopper(tenant, await signed_in(session_factory, tenant))
    assert (await client.get(CART, headers=someone)).json()["items"] == []
    async with session_factory() as session:
        carts = await session.scalar(
            select(func.count()).select_from(Cart).execution_options(**CROSS)
        )
    assert carts == 1

    off = await selling_store(session_factory, "fechada", flags={"checkout": False})
    closed = as_shopper(off, await signed_in(session_factory, off))
    assert (await client.get(CART, headers=closed)).status_code == 404
    assert (await client.get(CART, headers=as_shopper(tenant, None))).status_code == 401
