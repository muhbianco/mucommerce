"""Product tags (stage D): given by name, one per slug, filtered in the storefront."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.catalog.models import Tag
from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import Tenant
from tests.test_catalog import base, catalog_tenant, create_product, member_headers
from tests.test_storefront_catalog import published, set_access, store  # noqa: F401 (fixture)

CROSS = {CROSS_TENANT_OPTION: True}
CATALOG = "/api/v1/storefront/catalog"


async def test_tags_are_created_by_name_and_deduplicated_by_slug(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await create_product(
        client, tenant, headers, tags=["Sem Glúten", "  vegano ", "sem gluten", "Vegano"]
    )
    assert product["tags"] == [
        {"slug": "sem-gluten", "name": "Sem Glúten"},
        {"slug": "vegano", "name": "vegano"},
    ]
    other = await create_product(client, tenant, headers, name="Cookie", tags=["VEGANO"])
    assert other["tags"] == [{"slug": "vegano", "name": "vegano"}]  # the existing tag

    listed = await client.get(f"{base(tenant)}/tags", headers=headers)
    assert [t["slug"] for t in listed.json()] == ["sem-gluten", "vegano"]

    for bad in (["!!!"], [""], ["x" * 61], [f"t{i}" for i in range(21)]):
        response = await client.post(
            f"{base(tenant)}/products",
            json={"name": "X", "base_price_cents": 1, "tags": bad},
            headers=headers,
        )
        assert response.status_code == 422, bad


async def test_updating_tags_replaces_the_list_and_is_audited(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, _ = store
    product = await create_product(client, tenant, headers, tags=["vegano", "doce"])
    url = f"{base(tenant)}/products/{product['id']}"

    same = await client.patch(url, json={"tags": ["Doce", "vegano"]}, headers=headers)
    assert [t["slug"] for t in same.json()["tags"]] == ["doce", "vegano"]
    changed = await client.patch(url, json={"tags": ["zero açúcar"]}, headers=headers)
    assert changed.json()["tags"] == [{"slug": "zero-acucar", "name": "zero açúcar"}]
    untouched = await client.patch(url, json={"name": "Brownie 70%"}, headers=headers)
    assert [t["slug"] for t in untouched.json()["tags"]] == ["zero-acucar"]
    detail = await client.get(url, headers=headers)
    assert [t["slug"] for t in detail.json()["tags"]] == ["zero-acucar"]
    cleared = await client.patch(url, json={"tags": []}, headers=headers)
    assert cleared.json()["tags"] == []

    async with session_factory() as session:
        audits = (
            await session.execute(
                select(AuditLog.before_json, AuditLog.after_json)
                .where(AuditLog.entity_id == product["id"])
                .where(AuditLog.action == "product.updated")
                .order_by(AuditLog.id)
            )
        ).all()
    # The first PATCH named the same tags (no change, no audit); name-only PATCH has no tags.
    assert [a.after_json.get("tags") for a in audits] == [["zero-acucar"], None, []]
    assert audits[0].before_json["tags"] == ["doce", "vegano"]


async def test_storefront_filters_by_tag_and_lists_only_tags_in_use(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    store: tuple[Tenant, dict[str, str], dict[str, str]],  # noqa: F811
) -> None:
    tenant, headers, shopper = store
    brownie = await published(client, session_factory, tenant, headers, tags=["vegano"])
    await published(client, session_factory, tenant, headers, name="Pudim", tags=["clássico"])
    await create_product(client, tenant, headers, name="Rascunho", tags=["segredo"])

    tags = (await client.get(f"{CATALOG}/tags", headers=shopper)).json()
    assert [t["slug"] for t in tags] == ["classico", "vegano"]  # not the draft's tag

    page = await client.get(f"{CATALOG}/products", params={"tag": "vegano"}, headers=shopper)
    assert [c["slug"] for c in page.json()["items"]] == [brownie["slug"]]
    missing = await client.get(f"{CATALOG}/products", params={"tag": "nada"}, headers=shopper)
    assert missing.status_code == 404
    detail = (await client.get(f"{CATALOG}/products/{brownie['slug']}", headers=shopper)).json()
    assert detail["tags"] == [{"slug": "vegano", "name": "vegano"}]

    await set_access(session_factory, tenant, "whitelist")
    assert (await client.get(f"{CATALOG}/tags", headers=shopper)).status_code == 401


async def test_tags_are_per_tenant(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha = await catalog_tenant(session_factory, "alpha")
    beta = await catalog_tenant(session_factory, "beta")
    alpha_headers = await member_headers(client, session_factory, alpha)
    beta_headers = await member_headers(client, session_factory, beta)
    await create_product(client, alpha, alpha_headers, tags=["vegano"])
    await create_product(client, beta, beta_headers, tags=["vegano"])

    async with session_factory() as session:
        rows = (
            await session.execute(select(Tag.tenant_id, Tag.slug).execution_options(**CROSS))
        ).all()
    assert sorted(rows) == sorted([(alpha.id, "vegano"), (beta.id, "vegano")])
    beta_tags = await client.get(f"{base(beta)}/tags", headers=alpha_headers)
    assert beta_tags.status_code == 404
