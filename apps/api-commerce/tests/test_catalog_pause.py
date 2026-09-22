"""Paused products and variants (stage D): published but not for sale, with a reason."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog, OutboxEvent
from app.catalog.models import Product, ProductStatus, ProductVariant, VariantStatus
from app.catalog.storefront import product_availability, variant_availability
from app.core.scopes import TenantRole
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_catalog import base, create_product, member_headers
from tests.test_storefront_catalog import published, store  # noqa: F401 (fixture)

CROSS = {CROSS_TENANT_OPTION: True}
CATALOG = "/api/v1/storefront/catalog"


def test_paused_is_unavailable_whatever_the_stock() -> None:
    product = Product(status=ProductStatus.PAUSED, stock_policy="untracked")
    variant = ProductVariant(status=VariantStatus.ACTIVE, stock_policy=None)
    assert variant_availability(variant, product, None) == "unavailable"
    product.status = ProductStatus.ACTIVE
    variant.status = VariantStatus.PAUSED
    assert variant_availability(variant, product, None) == "unavailable"

    assert product_availability(["unavailable", "available"]) == "available"
    assert product_availability(["unavailable", "sold_out"]) == "sold_out"
    assert product_availability(["unavailable", "unavailable"]) == "unavailable"
    assert product_availability(["available"], paused=True) == "unavailable"
    assert product_availability([]) == "sold_out"


async def test_pause_keeps_the_product_listed_as_unavailable(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, shopper = store
    product = await published(client, session_factory, tenant, headers, stock_policy="untracked")
    url = f"{base(tenant)}/products/{product['id']}"

    paused = await client.post(
        f"{url}/pause", json={"reason": "Forno em manutenção"}, headers=headers
    )
    assert paused.status_code == 200, paused.text
    body = paused.json()
    assert (body["status"], body["paused_reason"]) == ("paused", "Forno em manutenção")
    assert body["paused_at"]

    cards = (await client.get(f"{CATALOG}/products", headers=shopper)).json()["items"]
    assert [(c["slug"], c["availability"]) for c in cards] == [(product["slug"], "unavailable")]
    detail = (await client.get(f"{CATALOG}/products/{product['slug']}", headers=shopper)).json()
    assert detail["availability"] == "unavailable"
    assert {v["availability"] for v in detail["variants"]} == {"unavailable"}
    assert "Forno" not in str(detail)  # the reason is for the panel only
    sitemap = (await client.get(f"{CATALOG}/sitemap", headers=shopper)).json()
    assert len(sitemap["products"]) == 1

    # Pausing twice is a no-op; publishing a paused product does not resume it.
    assert (await client.post(f"{url}/pause", headers=headers)).json()["paused_at"] == body[
        "paused_at"
    ]
    assert (await client.post(f"{url}/publish", headers=headers)).json()["status"] == "paused"
    # A published product keeps its price and at least one image while paused.
    assert (
        await client.patch(url, json={"base_price_cents": 0}, headers=headers)
    ).status_code == 409

    resumed = await client.post(f"{url}/resume", headers=headers)
    assert resumed.status_code == 200
    assert (resumed.json()["status"], resumed.json()["paused_reason"]) == ("active", None)
    cards = (await client.get(f"{CATALOG}/products", headers=shopper)).json()["items"]
    assert cards[0]["availability"] == "available"
    assert (await client.post(f"{url}/resume", headers=headers)).status_code == 200  # no-op


async def test_only_a_product_for_sale_can_be_paused(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    draft = await create_product(client, tenant, headers)
    url = f"{base(tenant)}/products/{draft['id']}"
    refused = await client.post(f"{url}/pause", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "invalid_transition"
    assert refused.json()["error"]["details"] == {"code": "not_published"}
    assert (await client.post(f"{url}/resume", headers=headers)).status_code == 409

    product = await published(client, session_factory, tenant, headers)
    url = f"{base(tenant)}/products/{product['id']}"
    await client.post(f"{url}/pause", json={"reason": "férias"}, headers=headers)
    unpublished = (await client.post(f"{url}/unpublish", headers=headers)).json()
    assert (unpublished["status"], unpublished["paused_reason"]) == ("inactive", None)

    blank = await client.post(f"{url}/pause", json={"reason": ""}, headers=headers)
    assert blank.status_code == 422
    long = await client.post(f"{url}/pause", json={"reason": "x" * 201}, headers=headers)
    assert long.status_code == 422


async def test_variant_pause_and_the_last_live_variant(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, shopper = store
    product = await published(client, session_factory, tenant, headers, stock_policy="untracked")
    variant_id = product["variants"][0]["id"]
    variant_url = f"{base(tenant)}/products/{product['id']}/variants/{variant_id}"

    paused = await client.post(
        f"{variant_url}/pause", json={"reason": "tamanho G"}, headers=headers
    )
    assert paused.status_code == 200
    variant = paused.json()["variants"][0]
    assert (variant["status"], variant["paused_reason"]) == ("paused", "tamanho G")
    card = (await client.get(f"{CATALOG}/products", headers=shopper)).json()["items"][0]
    assert card["availability"] == "unavailable"

    # A paused variant still counts as the live one a published product must keep.
    last = await client.patch(variant_url, json={"status": "inactive"}, headers=headers)
    assert last.status_code == 409

    # Setting the status by hand ends the pause (and its reason).
    active = (await client.patch(variant_url, json={"status": "active"}, headers=headers)).json()
    assert (active["variants"][0]["status"], active["variants"][0]["paused_reason"]) == (
        "active",
        None,
    )
    assert (await client.post(f"{variant_url}/resume", headers=headers)).status_code == 200

    other = await client.post(
        f"{base(tenant)}/products/{product['id']}/variants/{'0' * 36}/pause", headers=headers
    )
    assert other.status_code == 404


async def test_pause_is_audited_emitted_and_needs_the_publish_scope(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await published(client, session_factory, tenant, headers)
    url = f"{base(tenant)}/products/{product['id']}"
    variant_url = f"{url}/variants/{product['variants'][0]['id']}"

    ops = await member_headers(client, session_factory, tenant, TenantRole.OPS)
    assert (await client.post(f"{url}/pause", headers=ops)).status_code == 403

    await client.post(f"{url}/pause", json={"reason": "sem cacau"}, headers=headers)
    await client.post(f"{url}/pause", headers=headers)  # no-op: no audit, no event
    await client.post(f"{url}/resume", headers=headers)
    await client.post(f"{variant_url}/pause", headers=headers)
    await client.post(f"{variant_url}/resume", headers=headers)

    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent.event_type, OutboxEvent.payload)
                .where(OutboxEvent.aggregate_id == product["id"])
                .order_by(OutboxEvent.sequence)
                .execution_options(**CROSS)
            )
        ).all()
        actions = (
            await session.execute(
                select(AuditLog.action, AuditLog.after_json)
                .where(AuditLog.tenant_id == tenant.id)
                .where(AuditLog.action.like("product.%"))
                .order_by(AuditLog.id)
            )
        ).all()
    pause_events = events[2:]  # after product.created and product.published
    assert [e.event_type for e in pause_events] == [
        "product.paused",
        "product.resumed",
        "product.variant_paused",
        "product.variant_resumed",
    ]
    payload: dict[str, Any] = pause_events[0].payload
    assert payload["reason"] == "sem cacau" and payload["actor"].startswith("admin:")
    assert pause_events[2].payload["variant_id"] == product["variants"][0]["id"]
    assert [a.action for a in actions][-4:] == [
        "product.paused",
        "product.resumed",
        "product.variant_paused",
        "product.variant_resumed",
    ]
    assert actions[-4].after_json == {"status": "paused", "reason": "sem cacau"}


async def test_paused_filter_in_the_panel_list(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await published(client, session_factory, tenant, headers)
    await published(client, session_factory, tenant, headers, name="Cookie")
    await client.post(f"{base(tenant)}/products/{product['id']}/pause", headers=headers)
    page = await client.get(f"{base(tenant)}/products?status=paused", headers=headers)
    assert [p["id"] for p in page.json()["items"]] == [product["id"]]
