"""PricingService (stage E, S4): server-side price and problems of each line."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.fulfillment.service import FulfillmentChoice
from app.models.base import utcnow
from app.pricing.quote import LineInput, allocate, line_subtotal
from app.pricing.service import PricingService
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.resolver import TenantResolver
from tests.shoppers import selling_store
from tests.test_catalog import add_ready_image, base, member_headers
from tests.test_events import event_body
from tests.test_storefront_catalog import set_stock

U = 1000  # one unit in milli


def test_line_subtotal_rounds_half_up_and_allocate_sums_exactly() -> None:
    assert line_subtotal(1999, 333) == 666  # 665.667 → 666
    assert line_subtotal(1000, 1500) == 1500
    assert line_subtotal(3, 500) == 2  # 1.5 → 2
    assert allocate(100, [1, 1, 1]) == [34, 33, 33]
    assert allocate(10, [700, 300]) == [7, 3]
    assert allocate(0, [5, 5]) == [0, 0] and allocate(7, []) == []
    assert sum(allocate(999, [123, 456, 789])) == 999


async def product(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    headers: dict[str, str],
    **body: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": "Brownie", "base_price_cents": 1500} | body
    created = (await client.post(f"{base(tenant)}/products", json=payload, headers=headers)).json()
    await add_ready_image(session_factory, tenant, created["id"])
    published = await client.post(
        f"{base(tenant)}/products/{created['id']}/publish", headers=headers
    )
    assert published.status_code == 200, published.text
    return dict(published.json())


async def pricing(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, fn: Any
) -> Any:
    async with session_factory() as session:
        context = await TenantResolver(session).resolve_by_id(tenant.id)
        bind_session_tenant(session, tenant.id)
        return await fn(PricingService(session, context, utcnow()))


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str]]:
    tenant = await selling_store(
        session_factory,
        flags={"events": True},
        settings={
            "fulfillment": {
                "pickup": {"enabled": True, "locations": [{"name": "Loja", "address": "Rua A"}]},
                "min_order_cents": 2000,
            }
        },
    )
    return tenant, await member_headers(client, session_factory, tenant)


async def test_prices_promotions_modifiers_and_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str]],
) -> None:
    tenant, headers = shop
    now = datetime.now(UTC)
    brownie = await product(
        client,
        session_factory,
        tenant,
        headers,
        promo_price_cents=1200,
        promo_ends_at=(now + timedelta(days=1)).isoformat(),
    )
    with_mods = await client.put(
        f"{base(tenant)}/products/{brownie['id']}/modifiers",
        json={
            "groups": [
                {
                    "name": "Cobertura",
                    "min_select": 1,
                    "max_select": 1,
                    "modifiers": [{"name": "Chocolate", "price_cents": 300}, {"name": "Nada"}],
                }
            ]
        },
        headers=headers,
    )
    group = with_mods.json()["modifier_groups"][0]
    chocolate = group["modifiers"][0]["id"]
    variant = brownie["variants"][0]["id"]
    await set_stock(session_factory, variant, 3)

    async def run(service: PricingService) -> Any:
        return await service.price(
            [
                LineInput(variant, 2 * U, (chocolate,), key="a"),
                LineInput(variant, 1 * U, (chocolate,), key="b"),  # 3 of 3: still fits
                LineInput(variant, 1 * U, (chocolate,), key="c"),  # 4th: out of stock
                LineInput(variant, 1 * U, (), key="d"),  # required modifier missing
                LineInput(variant, 1500, (chocolate,), key="e"),  # half a unit
                LineInput("0" * 36, U, (), key="f"),
            ]
        )

    priced, problems = await pricing(session_factory, tenant, run)
    assert [(p.line.key, p.unit_cents, p.subtotal_cents) for p in priced] == [
        ("a", 1500, 3000),  # promo 1200 + 300
        ("b", 1500, 1500),
    ]
    assert priced[0].base.compare_at_cents == 1500 and priced[0].modifiers_unit_cents == 300
    assert [(p.line.key, p.code) for p in problems] == [
        ("c", "out_of_stock"),
        ("d", "invalid_modifiers"),
        ("e", "invalid_quantity"),
        ("f", "unavailable"),
    ]
    assert problems[0].detail["available_milli"] == 3000
    assert problems[1].detail["code"] == "modifier_group_limit"

    await client.post(f"{base(tenant)}/products/{brownie['id']}/pause", headers=headers)

    async def paused(service: PricingService) -> Any:
        return await service.price([LineInput(variant, U, (chocolate,))])

    assert [p.code for p in (await pricing(session_factory, tenant, paused))[1]] == ["unavailable"]


async def test_untracked_skips_stock_and_quote_needs_fulfillment(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str]],
) -> None:
    tenant, headers = shop
    cake = await product(client, session_factory, tenant, headers, stock_policy="made_to_order")
    variant = cake["variants"][0]["id"]
    lines = [LineInput(variant, 1 * U)]
    location = None

    async def without_choice(service: PricingService) -> Any:
        return await service.quote(lines, choice=None)

    quote = await pricing(session_factory, tenant, without_choice)
    assert (quote.subtotal_cents, quote.problems, quote.fulfillment) == (1500, [], None)
    assert quote.can_checkout is False

    async def locations(service: PricingService) -> Any:
        return service.tenant.settings["fulfillment"]["pickup"]["locations"][0]["id"]

    location = await pricing(session_factory, tenant, locations)

    async def pickup(service: PricingService) -> Any:
        return await service.quote(lines, choice=FulfillmentChoice("pickup", location))

    below = await pricing(session_factory, tenant, pickup)
    assert below.fulfillment is not None and below.fulfillment.problems == ("below_minimum",)
    lines = [LineInput(variant, 2 * U)]
    ok = await pricing(session_factory, tenant, pickup)
    assert (ok.total_cents, ok.can_checkout) == (3000, True)


async def test_tickets_sell_only_while_the_lot_is_on_sale(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str]],
) -> None:
    tenant, headers = shop
    show = (
        await client.post(
            f"{base(tenant)}/products",
            json={"name": "Show", "kind": "ticket", "base_price_cents": 0},
            headers=headers,
        )
    ).json()
    url = f"{base(tenant)}/products/{show['id']}"
    await client.put(f"{url}/event", json=event_body(), headers=headers)
    soon = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    lots = (
        await client.post(
            f"{url}/event/lots",
            json={"name": "1º", "price_cents": 5000, "quantity": 2},
            headers=headers,
        )
    ).json()
    later = (
        await client.post(
            f"{url}/event/lots",
            json={"name": "2º", "price_cents": 7000, "quantity": 5, "sales_starts_at": soon},
            headers=headers,
        )
    ).json()
    await add_ready_image(session_factory, tenant, show["id"])
    await client.post(f"{url}/publish", headers=headers)
    first, second = (lots["lots"][0]["variant_id"], later["lots"][1]["variant_id"])

    async def run(service: PricingService) -> Any:
        return await service.quote([LineInput(first, 2 * U), LineInput(second, U)], choice=None)

    quote = await pricing(session_factory, tenant, run)
    assert [(p.line.variant_id, p.event.lot_name if p.event else None) for p in quote.lines] == [
        (first, "1º")
    ]
    assert [(p.code, p.detail) for p in quote.problems] == [
        ("lot_not_on_sale", {"state": "upcoming"})
    ]
    # Tickets need no pickup or delivery.
    assert quote.fulfillment is not None and quote.fulfillment.type == "none"
