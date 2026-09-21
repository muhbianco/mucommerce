"""Catalog (phase 1, S3): products, default variant, categories, pricing, panel routes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog, OutboxEvent
from app.catalog.models import Product
from app.catalog.pricing import check_promotion, effective_price, variant_price
from app.core.exceptions import TenantContextMissingError, ValidationError
from app.core.scopes import TenantRole
from app.core.slugs import next_free_slug, slugify
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.repository import TenantRepository
from app.tenancy.service import Actor, TenantService
from tests.conftest import create_admin, create_tenant, login

CROSS = {CROSS_TENANT_OPTION: True}
T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# ----------------------------------------------------------------------------- pure helpers
def test_promotion_window_is_half_open() -> None:
    kwargs: dict[str, Any] = {
        "base_cents": 1000,
        "promo_cents": 800,
        "starts_at": T0,
        "ends_at": T0 + timedelta(days=1),
    }
    assert effective_price(**kwargs, now=T0 - timedelta(microseconds=1)).amount_cents == 1000
    at_start = effective_price(**kwargs, now=T0)
    assert (at_start.amount_cents, at_start.compare_at_cents, at_start.promo_active) == (
        800,
        1000,
        True,
    )
    assert at_start.promo_ends_at == T0 + timedelta(days=1)
    at_end = effective_price(**kwargs, now=T0 + timedelta(days=1))
    assert (at_end.amount_cents, at_end.compare_at_cents, at_end.promo_active) == (
        1000,
        None,
        False,
    )


def test_open_ended_and_invalid_promotions() -> None:
    open_ended = effective_price(
        base_cents=1000, promo_cents=900, starts_at=None, ends_at=None, now=T0
    )
    assert open_ended.amount_cents == 900
    # A "promotion" that is not cheaper never applies.
    not_cheaper = effective_price(
        base_cents=1000, promo_cents=1000, starts_at=None, ends_at=None, now=T0
    )
    assert not_cheaper.amount_cents == 1000 and not not_cheaper.promo_active


def test_variant_with_own_price_ignores_product_promotion() -> None:
    own = variant_price(
        variant_price_cents=1200,
        base_cents=1000,
        promo_cents=500,
        starts_at=None,
        ends_at=None,
        now=T0,
    )
    assert (own.amount_cents, own.promo_active) == (1200, False)
    inherited = variant_price(
        variant_price_cents=None,
        base_cents=1000,
        promo_cents=500,
        starts_at=None,
        ends_at=None,
        now=T0,
    )
    assert (inherited.amount_cents, inherited.promo_active) == (500, True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"promo_cents": 1000},
        {"promo_cents": 0},
        {"promo_cents": 500, "starts_at": T0, "ends_at": T0},
        {"promo_cents": None, "ends_at": T0},
    ],
)
def test_check_promotion_rejects_impossible_promotions(kwargs: dict[str, Any]) -> None:
    args: dict[str, Any] = {"base_cents": 1000, "starts_at": None, "ends_at": None} | kwargs
    with pytest.raises(ValidationError):
        check_promotion(**args)


def test_slugify_and_suffixes() -> None:
    assert slugify("Pão de Mel 🍯 (500g)") == "pao-de-mel-500g"
    assert slugify("!!!") == ""
    assert next_free_slug("bolo", {"bolo", "bolo-2"}) == "bolo-3"
    long = "a" * 160
    assert next_free_slug(long, {long}) == "a" * 158 + "-2"


# ----------------------------------------------------------------------------- fixtures
async def catalog_tenant(
    session_factory: async_sessionmaker[AsyncSession], slug: str, *, catalog: bool = True
) -> Tenant:
    tenant = await create_tenant(session_factory, slug)
    if catalog:
        async with session_factory() as session:
            service = TenantService(session)
            await service.set_features(
                await service.get_or_404(tenant.id), {"catalog": True}, Actor.system("tests")
            )
            await session.commit()
    return tenant


async def member_headers(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    role: TenantRole = TenantRole.OWNER,
) -> dict[str, str]:
    email = f"{role}@{tenant.slug}.test"
    await create_admin(session_factory, email, memberships={tenant.id: role})
    return await login(client, email)


def base(tenant: Tenant) -> str:
    return f"/api/v1/admin/tenants/{tenant.id}"


@pytest.fixture
async def shop(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> tuple[Tenant, dict[str, str]]:
    tenant = await catalog_tenant(session_factory, "alpha")
    return tenant, await member_headers(client, session_factory, tenant)


async def create_product(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], **body: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": "Brownie", "base_price_cents": 1500} | body
    response = await client.post(f"{base(tenant)}/products", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


# ----------------------------------------------------------------------------- products
async def test_create_generates_sku_slug_and_default_variant(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    first = await create_product(client, tenant, headers, name="Pão de Mel")
    assert first["sku"] == "P00001"
    assert first["slug"] == "pao-de-mel"
    assert first["status"] == "draft"
    assert first["price"] == {
        "amount_cents": 1500,
        "compare_at_cents": None,
        "promo_active": False,
        "promo_ends_at": None,
    }
    [variant] = first["variants"]
    assert (variant["sku"], variant["name"], variant["price_cents"]) == ("P00001", "Padrão", None)
    assert variant["price"]["amount_cents"] == 1500

    second = await create_product(client, tenant, headers, name="Pão de Mel")
    assert (second["sku"], second["slug"]) == ("P00002", "pao-de-mel-2")


async def test_generated_sku_skips_one_taken_by_hand(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    manual = await create_product(client, tenant, headers, sku="p00001")
    assert manual["sku"] == "P00001"  # normalised to upper case
    generated = await create_product(client, tenant, headers)
    assert generated["sku"] == "P00002"


async def test_sku_and_slug_are_unique_per_tenant_only(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str]],
) -> None:
    tenant, headers = shop
    await create_product(client, tenant, headers, sku="BRW-1", slug="brownie")
    dup_sku = await client.post(
        f"{base(tenant)}/products",
        json={"name": "Outro", "base_price_cents": 100, "sku": "brw-1"},
        headers=headers,
    )
    assert dup_sku.status_code == 409
    dup_slug = await client.post(
        f"{base(tenant)}/products",
        json={"name": "Outro", "base_price_cents": 100, "slug": "brownie"},
        headers=headers,
    )
    assert dup_slug.status_code == 409

    beta = await catalog_tenant(session_factory, "beta")
    beta_headers = await member_headers(client, session_factory, beta)
    same = await create_product(client, beta, beta_headers, sku="BRW-1", slug="brownie")
    assert same["sku"] == "BRW-1"


@pytest.mark.parametrize(
    "body",
    [
        {"promo_price_cents": 1500},
        {
            "promo_price_cents": 900,
            "promo_starts_at": "2026-10-02T00:00:00Z",
            "promo_ends_at": "2026-10-01T00:00:00Z",
        },
        {"promo_ends_at": "2026-10-01T00:00:00Z"},
        {"promo_price_cents": 900, "promo_starts_at": "2026-10-01T00:00:00"},  # naive
        {"sku": "com espaço"},
        {"slug": "Não-Slug"},
        {"base_price_cents": -1},
        {"base_price_cents": 1_000_000_001},
        {"inventado": True},
    ],
)
async def test_invalid_products_are_rejected(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]], body: dict[str, Any]
) -> None:
    tenant, headers = shop
    payload = {"name": "Brownie", "base_price_cents": 1500} | body
    response = await client.post(f"{base(tenant)}/products", json=payload, headers=headers)
    assert response.status_code == 422, response.text


async def test_patch_is_partial_and_guards_required_fields(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    product = await create_product(
        client, tenant, headers, promo_price_cents=1000, short_description="Macio"
    )
    await create_product(client, tenant, headers, name="Bolo", slug="bolo")
    url = f"{base(tenant)}/products/{product['id']}"

    renamed = await client.patch(url, json={"name": "Brownie de Nozes"}, headers=headers)
    assert renamed.status_code == 200
    body = renamed.json()
    assert (body["name"], body["short_description"], body["slug"]) == (
        "Brownie de Nozes",
        "Macio",
        "brownie",
    )
    assert body["price"]["amount_cents"] == 1000

    cleared = await client.patch(url, json={"promo_price_cents": None}, headers=headers)
    assert cleared.json()["price"]["amount_cents"] == 1500

    assert (await client.patch(url, json={"name": None}, headers=headers)).status_code == 422
    assert (await client.patch(url, json={"slug": "bolo"}, headers=headers)).status_code == 409
    assert (await client.patch(url, json={"sku": "X"}, headers=headers)).status_code == 422
    # The merged state is validated: a promo above the new base price is refused.
    await client.patch(url, json={"promo_price_cents": 1400}, headers=headers)
    lower = await client.patch(url, json={"base_price_cents": 1200}, headers=headers)
    assert lower.status_code == 422


async def test_publish_needs_price_and_keeps_first_publication_time(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    product = await create_product(client, tenant, headers, base_price_cents=0)
    url = f"{base(tenant)}/products/{product['id']}"

    blocked = await client.post(f"{url}/publish", headers=headers)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["details"] == {"missing": ["price"]}

    await client.patch(url, json={"base_price_cents": 2500}, headers=headers)
    published = await client.post(f"{url}/publish", headers=headers)
    assert published.status_code == 200
    first_published_at = published.json()["published_at"]
    assert published.json()["status"] == "active" and first_published_at

    zero = await client.patch(url, json={"base_price_cents": 0}, headers=headers)
    assert zero.status_code == 409

    unpublished = await client.post(f"{url}/unpublish", headers=headers)
    assert unpublished.json()["status"] == "inactive"
    again = await client.post(f"{url}/publish", headers=headers)
    assert again.json()["published_at"] == first_published_at


async def test_last_active_variant_of_a_published_product_stays_active(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    product = await create_product(client, tenant, headers)
    url = f"{base(tenant)}/products/{product['id']}"
    variant_url = f"{url}/variants/{product['variants'][0]['id']}"
    await client.post(f"{url}/publish", headers=headers)

    refused = await client.patch(variant_url, json={"status": "inactive"}, headers=headers)
    assert refused.status_code == 409

    priced = await client.patch(variant_url, json={"price_cents": 1800}, headers=headers)
    assert priced.status_code == 200
    assert priced.json()["variants"][0]["price"]["amount_cents"] == 1800


async def test_variant_with_own_price_does_not_follow_the_promotion(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    product = await create_product(client, tenant, headers, promo_price_cents=1000)
    variant_url = f"{base(tenant)}/products/{product['id']}/variants/{product['variants'][0]['id']}"
    body = (await client.patch(variant_url, json={"price_cents": 1400}, headers=headers)).json()
    assert body["price"]["amount_cents"] == 1000  # product card still shows the promotion
    assert body["variants"][0]["price"] == {
        "amount_cents": 1400,
        "compare_at_cents": None,
        "promo_active": False,
        "promo_ends_at": None,
    }


async def test_archive_hides_blocks_edits_and_is_idempotent(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    kept = await create_product(client, tenant, headers, name="Fica")
    gone = await create_product(client, tenant, headers, name="Sai")
    url = f"{base(tenant)}/products/{gone['id']}"

    assert (await client.delete(url, headers=headers)).status_code == 204
    assert (await client.delete(url, headers=headers)).status_code == 204

    listed = await client.get(f"{base(tenant)}/products", headers=headers)
    assert [p["id"] for p in listed.json()["items"]] == [kept["id"]]
    archived = await client.get(f"{base(tenant)}/products?status=archived", headers=headers)
    assert [p["id"] for p in archived.json()["items"]] == [gone["id"]]

    assert (await client.patch(url, json={"name": "X"}, headers=headers)).status_code == 409
    assert (await client.post(f"{url}/publish", headers=headers)).status_code == 409


async def test_list_pages_with_keyset_and_filters(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    category = await client.post(
        f"{base(tenant)}/categories", json={"name": "Doces"}, headers=headers
    )
    category_id = category.json()["id"]
    created = [
        await create_product(client, tenant, headers, name=f"Doce {i}", category_ids=[category_id])
        for i in range(3)
    ] + [await create_product(client, tenant, headers, name="Pão")]

    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": "3"} | ({"cursor": cursor} if cursor else {})
        page = (await client.get(f"{base(tenant)}/products", params=params, headers=headers)).json()
        seen += [item["id"] for item in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == [p["id"] for p in reversed(created)]

    by_name = await client.get(f"{base(tenant)}/products", params={"q": "pão"}, headers=headers)
    assert [p["name"] for p in by_name.json()["items"]] == ["Pão"]
    by_sku = await client.get(f"{base(tenant)}/products", params={"q": "p0000"}, headers=headers)
    assert len(by_sku.json()["items"]) == 4
    wildcard = await client.get(f"{base(tenant)}/products", params={"q": "%"}, headers=headers)
    assert wildcard.json()["items"] == []
    in_category = await client.get(
        f"{base(tenant)}/products", params={"category_id": category_id}, headers=headers
    )
    assert len(in_category.json()["items"]) == 3
    bad_cursor = await client.get(
        f"{base(tenant)}/products", params={"cursor": "lixo"}, headers=headers
    )
    assert bad_cursor.status_code == 422


async def test_idempotent_create_replays_the_first_response(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str]],
) -> None:
    tenant, headers = shop
    keyed = headers | {"Idempotency-Key": "form-123"}
    payload = {"name": "Brownie", "base_price_cents": 1500}
    first = await client.post(f"{base(tenant)}/products", json=payload, headers=keyed)
    second = await client.post(f"{base(tenant)}/products", json=payload, headers=keyed)
    assert first.status_code == second.status_code == 201
    assert second.headers.get("Idempotent-Replayed") == "true"
    assert second.json()["id"] == first.json()["id"]
    async with session_factory() as session:
        count = len((await session.execute(select(Product).execution_options(**CROSS))).all())
    assert count == 1


async def test_product_writes_are_audited_and_emitted(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    shop: tuple[Tenant, dict[str, str]],
) -> None:
    tenant, headers = shop
    product = await create_product(client, tenant, headers)
    url = f"{base(tenant)}/products/{product['id']}"
    await client.patch(url, json={"name": "Brownie 2"}, headers=headers)
    await client.patch(url, json={"name": "Brownie 2"}, headers=headers)  # no-op: no event
    await client.post(f"{url}/publish", headers=headers)
    await client.delete(url, headers=headers)

    async with session_factory() as session:
        events = (
            await session.execute(
                select(OutboxEvent.event_type, OutboxEvent.sequence)
                .where(OutboxEvent.aggregate_id == product["id"])
                .order_by(OutboxEvent.sequence)
                .execution_options(**CROSS)
            )
        ).all()
        actions = (
            await session.execute(
                select(AuditLog.action, AuditLog.actor, AuditLog.tenant_id)
                .where(AuditLog.entity_id == product["id"])
                .order_by(AuditLog.id)
            )
        ).all()
    assert [e.event_type for e in events] == [
        "product.created",
        "product.updated",
        "product.published",
        "product.archived",
    ]
    assert [e.sequence for e in events] == [1, 2, 3, 4]
    assert [a.action for a in actions] == [
        "product.created",
        "product.updated",
        "product.published",
        "product.archived",
    ]
    assert {a.tenant_id for a in actions} == {tenant.id}
    assert all(a.actor.startswith("admin:") for a in actions)


# ----------------------------------------------------------------------------- categories
async def test_categories_nest_two_levels_at_most(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    url = f"{base(tenant)}/categories"
    doces = (await client.post(url, json={"name": "Doces"}, headers=headers)).json()
    salgados = (await client.post(url, json={"name": "Salgados"}, headers=headers)).json()
    bolos = (
        await client.post(url, json={"name": "Bolos", "parent_id": doces["id"]}, headers=headers)
    ).json()
    assert bolos["parent_id"] == doces["id"]

    too_deep = await client.post(url, json={"name": "X", "parent_id": bolos["id"]}, headers=headers)
    assert too_deep.status_code == 422
    parent_with_children = await client.patch(
        f"{url}/{doces['id']}", json={"parent_id": salgados["id"]}, headers=headers
    )
    assert parent_with_children.status_code == 422
    self_parent = await client.patch(
        f"{url}/{salgados['id']}", json={"parent_id": salgados["id"]}, headers=headers
    )
    assert self_parent.status_code == 422
    moved = await client.patch(
        f"{url}/{bolos['id']}", json={"parent_id": salgados["id"]}, headers=headers
    )
    assert moved.json()["parent_id"] == salgados["id"]
    to_root = await client.patch(f"{url}/{bolos['id']}", json={"parent_id": None}, headers=headers)
    assert to_root.json()["parent_id"] is None

    listed = (await client.get(url, headers=headers)).json()
    assert [c["slug"] for c in listed] == ["bolos", "doces", "salgados"]


async def test_archiving_a_category_needs_no_children_and_unlinks_products(
    client: AsyncClient, shop: tuple[Tenant, dict[str, str]]
) -> None:
    tenant, headers = shop
    url = f"{base(tenant)}/categories"
    doces = (await client.post(url, json={"name": "Doces"}, headers=headers)).json()
    bolos = (
        await client.post(url, json={"name": "Bolos", "parent_id": doces["id"]}, headers=headers)
    ).json()
    product = await create_product(client, tenant, headers, category_ids=[bolos["id"], doces["id"]])
    assert sorted(product["category_ids"]) == sorted([bolos["id"], doces["id"]])

    assert (await client.delete(f"{url}/{doces['id']}", headers=headers)).status_code == 409
    assert (await client.delete(f"{url}/{bolos['id']}", headers=headers)).status_code == 204
    detail = await client.get(f"{base(tenant)}/products/{product['id']}", headers=headers)
    assert detail.json()["category_ids"] == [doces["id"]]
    # Archived categories can no longer be linked.
    relink = await client.patch(
        f"{base(tenant)}/products/{product['id']}",
        json={"category_ids": [bolos["id"]]},
        headers=headers,
    )
    assert relink.status_code == 422


# ----------------------------------------------------------------------------- access
async def test_catalog_routes_follow_the_flag_and_role_scopes(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    dark = await catalog_tenant(session_factory, "dark", catalog=False)
    dark_headers = await member_headers(client, session_factory, dark)
    off = await client.get(f"{base(dark)}/products", headers=dark_headers)
    assert off.status_code == 403
    assert off.json()["error"]["code"] == "feature_disabled"

    tenant = await catalog_tenant(session_factory, "alpha")
    owner = await member_headers(client, session_factory, tenant)
    product = await create_product(client, tenant, owner)

    ops = await member_headers(client, session_factory, tenant, TenantRole.OPS)
    assert (await create_product(client, tenant, ops, name="Da equipe"))["status"] == "draft"
    publish = await client.post(f"{base(tenant)}/products/{product['id']}/publish", headers=ops)
    assert publish.status_code == 403

    support = await member_headers(client, session_factory, tenant, TenantRole.SUPPORT)
    assert (await client.get(f"{base(tenant)}/products", headers=support)).status_code == 200
    write = await client.post(
        f"{base(tenant)}/products", json={"name": "X", "base_price_cents": 1}, headers=support
    )
    assert write.status_code == 403


# ----------------------------------------------------------------------------- sequences
async def test_tenant_sequence_counts_per_tenant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    alpha = await create_tenant(session_factory, "alpha")
    beta = await create_tenant(session_factory, "beta")
    async with session_factory() as session:
        repo = TenantRepository(session)
        with pytest.raises(TenantContextMissingError):
            await repo.next_sequence("product_sku")
        bind_session_tenant(session, alpha.id)
        assert [await repo.next_sequence("product_sku") for _ in range(3)] == [1, 2, 3]
        bind_session_tenant(session, beta.id)
        assert await repo.next_sequence("product_sku") == 1
        await session.commit()
    async with session_factory() as session:
        bind_session_tenant(session, alpha.id)
        assert await TenantRepository(session).next_sequence("product_sku") == 4
