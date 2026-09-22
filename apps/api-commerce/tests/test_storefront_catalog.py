"""Storefront API (phase 1, S7): published catalog by Host, access mode, landing, sitemap."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.inventory.models import InventoryBalance
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.test_catalog import add_ready_image, base, catalog_tenant, create_product, member_headers

CROSS = {CROSS_TENANT_OPTION: True}


async def set_access(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, mode: str
) -> None:
    async with session_factory() as session:
        service = TenantService(session)
        await service.set_setting(
            await service.get_or_404(tenant.id),
            "storefront",
            {"access_mode": mode},
            Actor.system("tests"),
        )
        await session.commit()


async def set_stock(
    session_factory: async_sessionmaker[AsyncSession], variant_id: str, units: int
) -> None:
    async with session_factory() as session:
        await session.execute(
            update(InventoryBalance)
            .where(InventoryBalance.variant_id == variant_id)
            .values(on_hand_milli=units * 1000)
            .execution_options(synchronize_session=False, **CROSS)
        )
        await session.commit()


async def published(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    headers: dict[str, str],
    **body: Any,
) -> dict[str, Any]:
    product = await create_product(client, tenant, headers, **body)
    await add_ready_image(session_factory, tenant, product["id"])
    response = await client.post(
        f"{base(tenant)}/products/{product['id']}/publish", headers=headers
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


@pytest.fixture
async def store(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str], dict[str, str]]:
    tenant = await catalog_tenant(session_factory, "alpha")
    await set_access(session_factory, tenant, "public")
    headers = await member_headers(client, session_factory, tenant)
    return tenant, headers, {"host": "alpha.loja.test"}


async def test_only_published_products_with_labels_not_numbers(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, headers, shopper = store
    brownie = await published(
        client,
        session_factory,
        tenant,
        headers,
        name="Brownie",
        promo_price_cents=1200,
        cost_cents_estimate=400,
    )
    await published(
        client,
        session_factory,
        tenant,
        headers,
        name="Bolo sob encomenda",
        stock_policy="made_to_order",
    )
    await create_product(client, tenant, headers, name="Rascunho")

    page = (await client.get("/api/v1/storefront/catalog/products", headers=shopper)).json()
    cards = {c["name"]: c for c in page["items"]}
    assert set(cards) == {"Brownie", "Bolo sob encomenda"}
    assert cards["Brownie"]["availability"] == "sold_out"
    assert cards["Bolo sob encomenda"]["availability"] == "made_to_order"
    assert cards["Brownie"]["price"] == {
        "amount_cents": 1200,
        "compare_at_cents": 1500,
        "promo_active": True,
        "promo_ends_at": None,
        "currency": "BRL",
    }
    assert cards["Brownie"]["image"]["renditions"][0]["url"].endswith("orig.webp")

    await set_stock(session_factory, brownie["variants"][0]["id"], 3)
    detail = await client.get(
        f"/api/v1/storefront/catalog/products/{brownie['slug']}", headers=shopper
    )
    body = detail.json()
    assert body["availability"] == "available"
    assert [v["availability"] for v in body["variants"]] == ["available"]
    assert "cost" not in detail.text and "on_hand" not in detail.text and "3000" not in detail.text

    draft = await client.get("/api/v1/storefront/catalog/products/rascunho", headers=shopper)
    assert draft.status_code == 404


async def test_access_mode_and_flags_gate_the_catalog(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, headers, shopper = store
    await published(client, session_factory, tenant, headers)
    await set_access(session_factory, tenant, "whitelist")
    url = "/api/v1/storefront/catalog/products"

    closed = await client.get(url, headers=shopper)
    assert closed.status_code == 401
    assert closed.json()["error"]["code"] == "login_required"
    # The web's internal token only picks the Host; it never grants access.
    via_web = await client.get(
        url,
        headers={
            "host": "api.test",
            "x-tenant-host": "alpha.loja.test",
            "x-internal-token": "web-token-test",
        },
    )
    assert via_web.status_code == 401

    async with session_factory() as session:
        service = TenantService(session)
        await service.set_features(
            await service.get_or_404(tenant.id), {"catalog": False}, Actor.system("tests")
        )
        await session.commit()
    await set_access(session_factory, tenant, "public")
    assert (await client.get(url, headers=shopper)).status_code == 404


async def test_keyset_paging_search_and_category_tree(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, headers, shopper = store
    doces = (
        await client.post(f"{base(tenant)}/categories", json={"name": "Doces"}, headers=headers)
    ).json()
    bolos = (
        await client.post(
            f"{base(tenant)}/categories",
            json={"name": "Bolos", "parent_id": doces["id"]},
            headers=headers,
        )
    ).json()
    names = []
    for i, (position, category) in enumerate([(2, bolos), (1, doces), (1, None), (3, None)]):
        body: dict[str, Any] = {"name": f"Item {i}", "position": position}
        if category:
            body["category_ids"] = [category["id"]]
        names.append((await published(client, session_factory, tenant, headers, **body))["name"])

    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": "2"} | ({"cursor": cursor} if cursor else {})
        page = (
            await client.get("/api/v1/storefront/catalog/products", params=params, headers=shopper)
        ).json()
        seen += [c["name"] for c in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == ["Item 1", "Item 2", "Item 0", "Item 3"]  # position, then creation

    in_doces = await client.get(
        "/api/v1/storefront/catalog/products", params={"category": "doces"}, headers=shopper
    )
    assert sorted(c["name"] for c in in_doces.json()["items"]) == ["Item 0", "Item 1"]  # + child
    missing = await client.get(
        "/api/v1/storefront/catalog/products", params={"category": "nao-existe"}, headers=shopper
    )
    assert missing.status_code == 404
    found = await client.get(
        "/api/v1/storefront/catalog/products", params={"q": "item 3"}, headers=shopper
    )
    assert [c["name"] for c in found.json()["items"]] == ["Item 3"]
    categories = (await client.get("/api/v1/storefront/catalog/categories", headers=shopper)).json()
    assert [c["slug"] for c in categories] == ["doces", "bolos"]

    sitemap = (await client.get("/api/v1/storefront/catalog/sitemap", headers=shopper)).json()
    assert len(sitemap["products"]) == 4 and sitemap["categories"] == ["bolos", "doces"]


async def test_other_tenants_products_are_not_served_by_this_host(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    _, _, shopper = store
    beta = await catalog_tenant(session_factory, "beta")
    await set_access(session_factory, beta, "public")
    beta_headers = await member_headers(client, session_factory, beta)
    theirs = await published(client, session_factory, beta, beta_headers, name="Segredo da Beta")
    response = await client.get(
        f"/api/v1/storefront/catalog/products/{theirs['slug']}", headers=shopper
    )
    assert response.status_code == 404
    listed = (await client.get("/api/v1/storefront/catalog/products", headers=shopper)).json()
    assert listed["items"] == []


async def test_landing_resolves_blocks_and_hides_catalog_blocks_when_closed(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],
) -> None:
    tenant, headers, shopper = store
    live = await published(client, session_factory, tenant, headers, name="Brownie")
    draft = await create_product(client, tenant, headers, name="Rascunho")
    landing = {
        "blocks": [
            {"type": "hero", "title": "Doces da Alpha", "cta_label": "Ver produtos"},
            {
                "type": "featured_products",
                "title": "Destaques",
                "product_ids": [draft["id"], live["id"]],
            },
            {"type": "contact", "whatsapp_e164": "+5511999998888"},
        ]
    }
    saved = await client.put(
        f"{base(tenant)}/settings/landing", json={"value": landing}, headers=headers
    )
    assert saved.status_code == 200, saved.text

    blocks = (await client.get("/api/v1/storefront/landing", headers=shopper)).json()
    assert [b["type"] for b in blocks] == ["hero", "featured_products", "contact"]
    assert [p["name"] for p in blocks[1]["products"]] == ["Brownie"]
    assert "product_ids" not in blocks[1]

    await set_access(session_factory, tenant, "whitelist")
    closed = (await client.get("/api/v1/storefront/landing", headers=shopper)).json()
    # The section stays, locked and empty: the visitor sees there is a catalog behind the login,
    # and no product name or id leaves the store.
    assert [b["type"] for b in closed] == ["hero", "featured_products", "contact"]
    assert closed[1] == {
        "type": "featured_products",
        "title": "Destaques",
        "products": [],
        "locked": True,
    }

    empty = await catalog_tenant(session_factory, "vazia")
    assert empty
    assert (
        await client.get("/api/v1/storefront/landing", headers={"host": "vazia.loja.test"})
    ).json() == []
