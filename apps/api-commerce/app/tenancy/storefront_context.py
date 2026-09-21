"""The tenant context the storefront renders with (public route and the web's internal one)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.media.service import pick_rendition, ready_media_by_ids
from app.schemas.internal import StorefrontContext, StorefrontTenant
from app.tenancy.context import TenantContext
from app.tenancy.models import DomainPurpose
from app.tenancy.repository import TenantRepository


async def _branding_and_seo(
    session: AsyncSession, tenant: TenantContext
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Uploaded images win over pasted URLs once processed; unready ones are omitted."""
    branding = dict(tenant.settings.get("branding", {}))
    seo = dict(tenant.settings.get("seo", {}))
    media = await ready_media_by_ids(
        session,
        [str(i) for i in (branding.get("logo_media_id"), seo.get("og_image_media_id")) if i],
    )
    logo = media.get(str(branding.get("logo_media_id")))
    if logo is not None:
        branding["logo"] = pick_rendition(logo, max_width=600)
    elif branding.get("logo_url"):
        branding["logo"] = {"url": branding["logo_url"], "width": None, "height": None}
    og = media.get(str(seo.get("og_image_media_id")))
    if og is not None and (image := pick_rendition(og, max_width=1200)):
        seo["og_image_url"] = image["url"]
    return branding, seo


async def build_storefront_context(
    session: AsyncSession, tenant: TenantContext, *, internal: bool
) -> StorefrontContext:
    primary = await TenantRepository(session).primary_domain(tenant.id, DomainPurpose.STOREFRONT)
    storefront = tenant.settings.get("storefront", {})
    branding, seo = await _branding_and_seo(session, tenant)
    features = (
        tenant.features
        if internal
        else {k: v for k, v in tenant.features.items() if not k.startswith("payments.")}
    )
    return StorefrontContext(
        tenant=StorefrontTenant(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            timezone=tenant.timezone,
            locale=tenant.locale,
            currency=tenant.currency,
        ),
        host=tenant.host,
        primary_host=primary.hostname if primary else None,
        access_mode=str(storefront.get("access_mode", "whitelist")),
        features=features,
        branding=branding,
        seo=seo,
        fulfillment=tenant.settings.get("fulfillment", {}),
        chatwoot_url=(
            settings.chatwoot_public_url if internal and tenant.feature("chatwoot") else None
        ),
    )
