"""Product options and the variant matrix (stage D)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.catalog.models import ProductVariant
from app.inventory.models import InventoryBalance
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_catalog import base, create_product
from tests.test_storefront_catalog import published, set_stock, store  # noqa: F401 (fixture)

CROSS = {CROSS_TENANT_OPTION: True}
CATALOG = "/api/v1/storefront/catalog"
SIZES = {"name": "Tamanho", "values": ["P", "M"]}
COLORS = {"name": "Cor", "values": ["Azul", "Verde"]}


def combos(product: dict[str, Any]) -> list[tuple[str, str, dict[str, str] | None]]:
    return [(v["sku"], v["name"], v["option_values"]) for v in product["variants"]]


async def put_options(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], product_id: str, options: list
) -> Any:
    return await client.put(
        f"{base(tenant)}/products/{product_id}/options",
        json={"options": options},
        headers=headers,
    )


async def test_matrix_reuses_the_default_variant_and_names_by_values(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await create_product(client, tenant, headers)
    default_id = product["variants"][0]["id"]
    sku = product["sku"]

    response = await put_options(client, tenant, headers, product["id"], [SIZES, COLORS])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["has_variants"] is True
    assert body["options"] == [SIZES, COLORS]
    assert combos(body) == [
        (sku, "P / Azul", {"Tamanho": "P", "Cor": "Azul"}),
        (f"{sku}-1", "P / Verde", {"Tamanho": "P", "Cor": "Verde"}),
        (f"{sku}-2", "M / Azul", {"Tamanho": "M", "Cor": "Azul"}),
        (f"{sku}-3", "M / Verde", {"Tamanho": "M", "Cor": "Verde"}),
    ]
    assert body["variants"][0]["id"] == default_id  # its stock and SKU carry over

    async with session_factory() as session:
        balances = (
            await session.execute(select(InventoryBalance.variant_id).execution_options(**CROSS))
        ).scalars()
        assert {v["id"] for v in body["variants"]} <= set(balances)

    again = await put_options(client, tenant, headers, product["id"], [SIZES, COLORS])
    assert combos(again.json()) == combos(body)  # same options: no-op


async def test_dropping_a_value_archives_and_bringing_it_back_revives(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await create_product(client, tenant, headers)
    first = (await put_options(client, tenant, headers, product["id"], [SIZES])).json()
    m_id = first["variants"][1]["id"]

    smaller = await put_options(
        client, tenant, headers, product["id"], [{"name": "Tamanho", "values": ["P", "G"]}]
    )
    sku = product["sku"]
    assert [(v["sku"], v["name"]) for v in smaller.json()["variants"]] == [
        (sku, "P"),
        (f"{sku}-2", "G"),
    ]
    async with session_factory() as session:
        archived = (
            await session.execute(
                select(ProductVariant.status, ProductVariant.archived_at)
                .where(ProductVariant.id == m_id)
                .execution_options(**CROSS)
            )
        ).one()
    assert archived.status == "inactive" and archived.archived_at is not None

    back = await put_options(
        client, tenant, headers, product["id"], [{"name": "Tamanho", "values": ["P", "M", "G"]}]
    )
    revived = [v for v in back.json()["variants"] if v["name"] == "M"]
    assert [(v["id"], v["status"]) for v in revived] == [(m_id, "active")]

    single = (await put_options(client, tenant, headers, product["id"], [])).json()
    assert (single["has_variants"], single["options"]) == (False, [])
    assert combos(single) == [(sku, "Padrão", None)]

    async with session_factory() as session:
        audit = (
            (
                await session.execute(
                    select(AuditLog.after_json)
                    .where(AuditLog.entity_id == product["id"])
                    .where(AuditLog.action == "product.options_updated")
                    .order_by(AuditLog.id)
                )
            )
            .scalars()
            .all()
        )
    assert audit[1]["archived"] == [f"{sku}-1"] and audit[1]["created"] == [f"{sku}-2"]


async def test_options_are_validated(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await create_product(client, tenant, headers)
    many = [{"name": f"O{i}", "values": [str(v) for v in range(5)]} for i in range(3)]
    for options in (
        [{"name": "Cor", "values": ["Azul", "azul"]}],
        [{"name": "Cor", "values": ["Azul"]}, {"name": "cor", "values": ["Verde"]}],
        [{"name": "Cor", "values": []}],
        [*many, {"name": "Extra", "values": ["x"]}],  # 4 options
        many,  # 125 combinations
    ):
        response = await put_options(client, tenant, headers, product["id"], options)
        assert response.status_code == 422, options
    archived = await create_product(client, tenant, headers, name="Velho")
    await client.delete(f"{base(tenant)}/products/{archived['id']}", headers=headers)
    refused = await put_options(client, tenant, headers, archived["id"], [SIZES])
    assert refused.status_code == 409


async def test_storefront_shows_options_and_each_variant_combination(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, shopper = store
    product = await published(client, session_factory, tenant, headers)
    body = (await put_options(client, tenant, headers, product["id"], [SIZES])).json()
    await set_stock(session_factory, body["variants"][1]["id"], 3)  # only M in stock

    detail = (await client.get(f"{CATALOG}/products/{product['slug']}", headers=shopper)).json()
    assert detail["options"] == [SIZES]
    assert [(v["option_values"], v["availability"]) for v in detail["variants"]] == [
        ({"Tamanho": "P"}, "sold_out"),
        ({"Tamanho": "M"}, "available"),
    ]
    assert detail["availability"] == "available"
