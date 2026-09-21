"""Tenant-editable settings (phase 1, S6): branding, landing, SEO; references and context."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.models import AuditLog
from app.core.scopes import TenantRole
from app.media.models import MediaAsset, MediaStatus
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from tests.test_catalog import base, catalog_tenant, create_product, member_headers


async def put(
    client: AsyncClient, tenant: Tenant, headers: dict[str, str], key: str, value: Any
) -> Any:
    return await client.put(
        f"{base(tenant)}/settings/{key}", json={"value": value}, headers=headers
    )


async def ready_image(
    session_factory: async_sessionmaker[AsyncSession], tenant: Tenant, owner_type: str
) -> str:
    async with session_factory() as session:
        bind_session_tenant(session, tenant.id)
        media = MediaAsset(
            owner_type=owner_type,
            status=MediaStatus.READY,
            declared_mime="image/png",
            declared_bytes=1,
            upload_key=f"incoming/{tenant.id}/x",
            renditions={
                "orig": {
                    "key": f"tenants/{tenant.id}/media/a/orig.webp",
                    "width": 1600,
                    "height": 800,
                },
                "w1200": {
                    "key": f"tenants/{tenant.id}/media/a/w1200.webp",
                    "width": 1200,
                    "height": 600,
                },
                "w600": {
                    "key": f"tenants/{tenant.id}/media/a/w600.webp",
                    "width": 600,
                    "height": 300,
                },
            },
        )
        session.add(media)
        await session.commit()
        return media.id


async def test_branding_and_seo_resolve_uploaded_images_in_the_context(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    logo = await ready_image(session_factory, tenant, "tenant_brand")

    saved = await put(
        client, tenant, headers, "branding", {"primary_color": "#aa3355", "logo_media_id": logo}
    )
    assert saved.status_code == 200, saved.text
    await put(client, tenant, headers, "seo", {"title": "Alpha Doces", "og_image_media_id": logo})

    context = (
        await client.get("/api/v1/storefront/context", headers={"host": "alpha.loja.test"})
    ).json()
    assert context["branding"]["primary_color"] == "#aa3355"
    assert context["branding"]["logo"]["url"].endswith("/w600.webp")
    assert context["seo"]["og_image_url"].endswith("/w1200.webp")
    internal = await client.get(
        "/api/v1/internal/storefront/context",
        headers={
            "host": "api.test",
            "x-tenant-host": "alpha.loja.test",
            "x-internal-token": "web-token-test",
        },
    )
    assert internal.json()["branding"]["logo"] == context["branding"]["logo"]

    listed = (await client.get(f"{base(tenant)}/settings", headers=headers)).json()
    assert listed["branding"]["logo_media_id"] == logo


async def test_references_must_be_the_tenants_own_rows(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    alpha = await catalog_tenant(session_factory, "alpha")
    beta = await catalog_tenant(session_factory, "beta")
    headers = await member_headers(client, session_factory, alpha)
    beta_headers = await member_headers(client, session_factory, beta)

    product_image = await ready_image(session_factory, alpha, "product")
    wrong_kind = await put(client, alpha, headers, "branding", {"logo_media_id": product_image})
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["error"]["details"]["media_ids"] == [product_image]

    foreign_logo = await ready_image(session_factory, beta, "tenant_brand")
    foreign = await put(client, alpha, headers, "branding", {"logo_media_id": foreign_logo})
    assert foreign.status_code == 422

    mine = await create_product(client, alpha, headers)
    theirs = await create_product(client, beta, beta_headers)
    landing = {
        "blocks": [
            {"type": "hero", "title": "Doces da Alpha", "cta_label": "Ver produtos"},
            {
                "type": "featured_products",
                "title": "Destaques",
                "product_ids": [mine["id"], theirs["id"]],
            },
        ]
    }
    refused = await put(client, alpha, headers, "landing", landing)
    assert refused.json()["error"]["details"]["product_ids"] == [theirs["id"]]

    landing["blocks"][1]["product_ids"] = [mine["id"]]
    assert (await put(client, alpha, headers, "landing", landing)).status_code == 200

    await client.delete(f"{base(alpha)}/products/{mine['id']}", headers=headers)
    archived = await put(client, alpha, headers, "landing", landing)
    assert archived.status_code == 422


async def test_only_tenant_keys_and_the_right_role(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    owner = await member_headers(client, session_factory, tenant)
    support = await member_headers(client, session_factory, tenant, TenantRole.SUPPORT)

    for key in ("storefront", "checkout", "fulfillment", "inventado"):
        assert (await put(client, tenant, owner, key, {})).status_code == 422, key
    assert (await put(client, tenant, support, "branding", {})).status_code == 403
    invalid = await put(
        client, tenant, owner, "landing", {"blocks": [{"type": "html", "body": "<script>"}]}
    )
    assert invalid.status_code == 422
    too_many = await put(
        client, tenant, owner, "landing", {"blocks": [{"type": "text", "body": "x"}] * 13}
    )
    assert too_many.status_code == 422


async def test_large_landing_is_summarised_in_the_audit_log(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await catalog_tenant(session_factory, "alpha")
    headers = await member_headers(client, session_factory, tenant)
    body = {"blocks": [{"type": "text", "body": "a" * 2000} for _ in range(4)]}
    assert (await put(client, tenant, headers, "landing", body)).status_code == 200
    async with session_factory() as session:
        entry = (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.action == "tenant.settings_changed")
                .order_by(AuditLog.id.desc())
                .limit(1)
            )
        ).scalar_one()
    assert entry.after_json is not None
    assert entry.after_json["landing"]["_summary"] is True
