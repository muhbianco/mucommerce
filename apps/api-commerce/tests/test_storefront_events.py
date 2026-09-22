"""Storefront events (stage D): upcoming list, event page, sitemap; flag and access mode."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import add_ready_image, base, create_product, member_headers
from tests.test_events import event_body, events_tenant
from tests.test_storefront_catalog import set_access

CATALOG = "/api/v1/storefront/catalog"
SHOPPER = {"host": "alpha.loja.test"}
NOW = datetime.now(UTC).replace(microsecond=0)


async def ticket(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    headers: dict[str, str],
    name: str,
    starts_at: datetime,
    lots: list[dict[str, Any]],
    **event: Any,
) -> dict[str, Any]:
    product = await create_product(
        client, tenant, headers, name=name, kind="ticket", base_price_cents=0
    )
    url = f"{base(tenant)}/products/{product['id']}"
    body = event_body(
        starts_at=starts_at.isoformat(), ends_at=(starts_at + timedelta(hours=2)).isoformat()
    )
    response = await client.put(f"{url}/event", json=body | event, headers=headers)
    assert response.status_code == 200, response.text
    for lot in lots:
        assert (
            await client.post(f"{url}/event/lots", json=lot, headers=headers)
        ).status_code == 201
    await add_ready_image(session_factory, tenant, product["id"])
    assert (await client.post(f"{url}/publish", headers=headers)).status_code == 200
    return product


async def test_upcoming_events_page_by_date_with_the_cheapest_lot_on_sale(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await events_tenant(session_factory)
    await set_access(session_factory, tenant, "public")
    headers = await member_headers(client, session_factory, tenant)
    later = await ticket(
        client,
        session_factory,
        tenant,
        headers,
        "Réveillon",
        NOW + timedelta(days=60),
        [
            {"name": "Antecipado", "price_cents": 8000, "quantity": 10},
            {
                "name": "Promo relâmpago",
                "price_cents": 5000,
                "quantity": 10,
                "sales_starts_at": (NOW + timedelta(days=5)).isoformat(),
            },
        ],
        online_url="https://ao-vivo.example/reveillon",
    )
    sooner = await ticket(
        client,
        session_factory,
        tenant,
        headers,
        "Show de Natal",
        NOW + timedelta(days=10),
        [{"name": "Único", "price_cents": 3000, "quantity": 0}],
    )
    await ticket(  # over: not listed, page still up
        client,
        session_factory,
        tenant,
        headers,
        "Carnaval passado",
        NOW - timedelta(days=3),
        [{"name": "Único", "price_cents": 1000, "quantity": 5}],
    )

    first = await client.get(f"{CATALOG}/events", params={"limit": 1}, headers=SHOPPER)
    assert first.status_code == 200, first.text
    [natal] = first.json()["items"]
    assert (natal["slug"], natal["availability"]) == (sooner["slug"], "sold_out")
    rest = await client.get(
        f"{CATALOG}/events", params={"cursor": first.json()["next_cursor"]}, headers=SHOPPER
    )
    [reveillon] = rest.json()["items"]
    assert rest.json()["next_cursor"] is None
    assert (reveillon["slug"], reveillon["availability"]) == (later["slug"], "on_sale")
    assert reveillon["price_from"]["amount_cents"] == 8000  # the 5000 lot is not on sale yet
    assert reveillon["online"] is True and "ao-vivo" not in str(rest.json())

    page = await client.get(f"{CATALOG}/events/{later['slug']}", headers=SHOPPER)
    lots = page.json()["lots"]
    assert [(lot["name"], lot["state"]) for lot in lots] == [
        ("Antecipado", "on_sale"),
        ("Promo relâmpago", "upcoming"),
    ]
    assert "quantity" not in lots[0] and "available" not in lots[0]
    past = await client.get(f"{CATALOG}/events/carnaval-passado", headers=SHOPPER)
    assert past.json()["availability"] == "ended"
    assert (await client.get(f"{CATALOG}/events/nada", headers=SHOPPER)).status_code == 404

    sitemap = (await client.get(f"{CATALOG}/sitemap", headers=SHOPPER)).json()
    assert len(sitemap["events"]) == 3
    assert not {e["slug"] for e in sitemap["events"]} & {p["slug"] for p in sitemap["products"]}
    cards = (await client.get(f"{CATALOG}/products", headers=SHOPPER)).json()["items"]
    assert {card["kind"] for card in cards} == {"ticket"}


async def test_events_need_the_flag_and_follow_the_access_mode(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await events_tenant(session_factory)
    await set_access(session_factory, tenant, "public")
    headers = await member_headers(client, session_factory, tenant)
    await ticket(
        client,
        session_factory,
        tenant,
        headers,
        "Oficina",
        NOW + timedelta(days=3),
        [{"name": "Vaga", "price_cents": 100, "quantity": 5}],
    )
    assert (await client.get(f"{CATALOG}/events", headers=SHOPPER)).status_code == 200

    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"events": False}, Actor.system("tests")
        )
        await session.commit()
    assert (await client.get(f"{CATALOG}/events", headers=SHOPPER)).status_code == 404
    assert (await client.get(f"{CATALOG}/events/oficina", headers=SHOPPER)).status_code == 404
    sitemap = (await client.get(f"{CATALOG}/sitemap", headers=SHOPPER)).json()
    assert sitemap["events"] == [] and len(sitemap["products"]) == 1

    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"events": True}, Actor.system("tests")
        )
        await session.commit()
    await set_access(session_factory, tenant, "whitelist")
    assert (await client.get(f"{CATALOG}/events", headers=SHOPPER)).status_code == 401
