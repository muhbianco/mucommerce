"""Events (stage D): a ticket product's date, venue, capacity and lots of tickets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.catalog.events import lot_state
from app.catalog.models import Event, EventLot
from app.inventory.models import InventoryBalance, InventoryMovement
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import add_ready_image, base, catalog_tenant, create_product, member_headers

CROSS = {CROSS_TENANT_OPTION: True}
T0 = datetime(2026, 12, 5, 20, 0, tzinfo=UTC)
SOON = (datetime.now(UTC) + timedelta(days=30)).replace(microsecond=0)


# ----------------------------------------------------------------------------- pure lot state
def state(now: datetime, **overrides: Any) -> str:
    event = Event(starts_at=T0, ends_at=T0 + timedelta(hours=4), status="scheduled")
    lot = EventLot(sales_starts_at=T0 - timedelta(days=10), sales_ends_at=None)
    args: dict[str, Any] = {
        "available": 5,
        "variant_status": "active",
        "product_status": "active",
    } | overrides
    for name in ("sales_starts_at", "sales_ends_at"):
        if name in args:
            setattr(lot, name, args.pop(name))
    if "event_status" in args:
        event.status = args.pop("event_status")
    return lot_state(event=event, lot=lot, now=now, **args)


def test_a_lot_sells_inside_its_window_and_before_the_event_ends() -> None:
    assert state(T0 - timedelta(days=11)) == "upcoming"
    assert state(T0 - timedelta(days=1)) == "on_sale"
    assert state(T0 + timedelta(hours=1)) == "on_sale"  # at the door, until it ends
    assert state(T0 + timedelta(hours=4)) == "ended"
    assert state(T0 - timedelta(days=1), available=0) == "sold_out"
    assert state(T0 - timedelta(days=1), sales_ends_at=T0 - timedelta(days=2)) == "ended"
    assert state(T0 - timedelta(days=1), event_status="postponed") == "unavailable"
    assert state(T0 - timedelta(days=1), product_status="paused") == "unavailable"
    assert state(T0 - timedelta(days=1), variant_status="paused") == "unavailable"


# ----------------------------------------------------------------------------- panel API
async def events_tenant(
    session_factory: async_sessionmaker[AsyncSession], slug: str = "alpha", *, events: bool = True
) -> Tenant:
    tenant = await catalog_tenant(session_factory, slug)
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"events": events}, Actor.system("tests")
        )
        await session.commit()
    return tenant


@pytest.fixture
async def venue(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str], dict[str, Any]]:
    tenant = await events_tenant(session_factory)
    headers = await member_headers(client, session_factory, tenant)
    product = await create_product(
        client, tenant, headers, name="Show de Natal", kind="ticket", base_price_cents=0
    )
    return tenant, headers, product


def event_body(**overrides: Any) -> dict[str, Any]:
    return {
        "starts_at": SOON.isoformat(),
        "ends_at": (SOON + timedelta(hours=3)).isoformat(),
        "venue_name": "Teatro Municipal",
        "city": "São Paulo",
        "capacity": 100,
    } | overrides


async def test_event_and_lots_drive_the_ticket_stock(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    venue: tuple[Tenant, dict[str, str], dict[str, Any]],
) -> None:
    tenant, headers, product = venue
    url = f"{base(tenant)}/products/{product['id']}/event"
    assert (await client.get(url, headers=headers)).status_code == 404

    created = await client.put(url, json=event_body(), headers=headers)
    assert created.status_code == 200, created.text
    assert (created.json()["capacity"], created.json()["lots"]) == (100, [])

    first = await client.post(
        f"{url}/lots",
        json={"name": "1º lote", "price_cents": 5000, "quantity": 60},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    [lot1] = first.json()["lots"]
    assert (lot1["sku"], lot1["available"], lot1["state"]) == (product["sku"], 60, "unavailable")
    assert lot1["variant_id"] == product["variants"][0]["id"]  # took over the default variant

    second = await client.post(
        f"{url}/lots",
        json={"name": "2º lote", "price_cents": 7000, "quantity": 40},
        headers=headers,
    )
    lot2 = second.json()["lots"][1]
    assert (lot2["sku"], second.json()["allocated"]) == (f"{product['sku']}-1", 100)

    over = await client.post(
        f"{url}/lots", json={"name": "Extra", "price_cents": 1, "quantity": 1}, headers=headers
    )
    assert over.status_code == 409 and over.json()["error"]["details"]["code"] == "over_capacity"
    smaller = await client.put(url, json=event_body(capacity=90), headers=headers)
    assert smaller.status_code == 409

    # 10 tickets of lot 1 are gone (a sale, until orders exist): quantity cannot drop below that.
    async with session_factory() as session:
        await session.execute(
            update(InventoryBalance)
            .where(InventoryBalance.variant_id == lot1["variant_id"])
            .values(on_hand_milli=50_000)
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()
    too_low = await client.patch(f"{url}/lots/{lot1['id']}", json={"quantity": 5}, headers=headers)
    assert too_low.status_code == 409
    lowered = await client.patch(
        f"{url}/lots/{lot1['id']}", json={"quantity": 30, "price_cents": 5500}, headers=headers
    )
    assert lowered.status_code == 200, lowered.text
    lot1 = lowered.json()["lots"][0]
    assert (lot1["quantity"], lot1["available"], lot1["price_cents"]) == (30, 20, 5500)
    sold = await client.delete(f"{url}/lots/{lot1['id']}", headers=headers)
    assert sold.status_code == 409 and sold.json()["error"]["details"]["code"] == "sold"

    removed = await client.delete(f"{url}/lots/{lot2['id']}", headers=headers)
    assert [lot["name"] for lot in removed.json()["lots"]] == ["1º lote"]

    async with session_factory() as session:
        movements = (
            await session.execute(
                select(InventoryMovement.qty_milli, InventoryMovement.reason)
                .where(InventoryMovement.variant_id == lot1["variant_id"])
                .order_by(InventoryMovement.id)
                .execution_options(**CROSS)
            )
        ).all()
        actions = (
            (
                await session.execute(
                    select(AuditLog.action)
                    .where(AuditLog.entity_type == "event")
                    .order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    assert [m.qty_milli for m in movements] == [60_000, -30_000]
    assert movements[1].reason == "Lote 1º lote: de 60 para 30"
    assert actions == [
        "event.created",
        "event.lot_created",
        "event.lot_created",
        "event.lot_updated",
        "event.lot_removed",
    ]


async def test_a_ticket_publishes_only_with_a_lot_and_takes_no_options(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    venue: tuple[Tenant, dict[str, str], dict[str, Any]],
) -> None:
    tenant, headers, product = venue
    product_url = f"{base(tenant)}/products/{product['id']}"
    await add_ready_image(session_factory, tenant, product["id"])

    blocked = await client.post(f"{product_url}/publish", headers=headers)
    assert blocked.json()["error"]["details"] == {"missing": ["event"]}

    await client.put(f"{product_url}/event", json=event_body(), headers=headers)
    await client.post(
        f"{product_url}/event/lots",
        json={"name": "Único", "price_cents": 0, "quantity": 10},  # a free event
        headers=headers,
    )
    published = await client.post(f"{product_url}/publish", headers=headers)
    assert published.status_code == 200, published.text
    event = (await client.get(f"{product_url}/event", headers=headers)).json()
    assert event["lots"][0]["state"] == "on_sale"

    options = await client.put(
        f"{product_url}/options",
        json={"options": [{"name": "Setor", "values": ["A", "B"]}]},
        headers=headers,
    )
    assert options.status_code == 409


async def test_event_rules_and_the_flag(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    venue: tuple[Tenant, dict[str, str], dict[str, Any]],
) -> None:
    tenant, headers, product = venue
    url = f"{base(tenant)}/products/{product['id']}/event"
    for bad in (
        event_body(ends_at=SOON.isoformat()),  # ends when it starts
        event_body(venue_name=None),  # neither venue nor link
        event_body(online_url="http://inseguro.test"),
        event_body(starts_at="2026-12-05T20:00:00"),  # no offset
    ):
        assert (await client.put(url, json=bad, headers=headers)).status_code == 422, bad
    online = event_body(venue_name=None, online_url="https://meet.example/abc", capacity=None)
    assert (await client.put(url, json=online, headers=headers)).status_code == 200

    brownie = await create_product(client, tenant, headers, name="Brownie")
    not_ticket = await client.put(
        f"{base(tenant)}/products/{brownie['id']}/event", json=event_body(), headers=headers
    )
    assert not_ticket.json()["error"]["details"] == {"code": "not_a_ticket"}

    window = await client.post(
        f"{url}/lots",
        json={
            "name": "Lote",
            "price_cents": 1,
            "quantity": 1,
            "sales_starts_at": SOON.isoformat(),
            "sales_ends_at": (SOON - timedelta(days=1)).isoformat(),
        },
        headers=headers,
    )
    assert window.status_code == 422

    dark = await events_tenant(session_factory, "dark", events=False)
    dark_headers = await member_headers(client, session_factory, dark)
    ticket = await create_product(client, dark, dark_headers, kind="ticket")
    off = await client.put(
        f"{base(dark)}/products/{ticket['id']}/event", json=event_body(), headers=dark_headers
    )
    assert off.status_code == 403 and off.json()["error"]["code"] == "feature_disabled"
    other = await client.get(f"{base(dark)}/products/{product['id']}/event", headers=headers)
    assert other.status_code == 404


async def test_a_ticket_with_an_event_keeps_its_kind(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    venue: tuple[Tenant, dict[str, str], dict[str, Any]],
) -> None:
    tenant, headers, product = venue
    url = f"{base(tenant)}/products/{product['id']}"
    await client.put(f"{url}/event", json=event_body(), headers=headers)
    changed = await client.patch(url, json={"kind": "physical"}, headers=headers)
    assert changed.json()["error"]["details"] == {"code": "has_event"}

    shirt = await create_product(client, tenant, headers, name="Camiseta")
    await client.put(
        f"{base(tenant)}/products/{shirt['id']}/options",
        json={"options": [{"name": "Tamanho", "values": ["P", "M"]}]},
        headers=headers,
    )
    to_ticket = await client.patch(
        f"{base(tenant)}/products/{shirt['id']}", json={"kind": "ticket"}, headers=headers
    )
    assert to_ticket.json()["error"]["details"] == {"code": "has_options"}
