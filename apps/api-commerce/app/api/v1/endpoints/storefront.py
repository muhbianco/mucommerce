from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, StorefrontTenant
from app.schemas.internal import StorefrontContext
from app.schemas.internal import StorefrontTenant as StorefrontTenantRead
from app.tenancy.models import DomainPurpose
from app.tenancy.repository import TenantRepository

router = APIRouter(prefix="/storefront", tags=["Vitrine (público)"])


@router.get(
    "/context",
    response_model=StorefrontContext,
    summary="Contexto público do tenant resolvido pelo Host",
)
async def public_context(session: DbSession, tenant: StorefrontTenant) -> StorefrontContext:
    primary = await TenantRepository(session).primary_domain(tenant.id, DomainPurpose.STOREFRONT)
    storefront = tenant.settings.get("storefront", {})
    return StorefrontContext(
        tenant=StorefrontTenantRead(
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
        features={k: v for k, v in tenant.features.items() if not k.startswith("payments.")},
        branding=tenant.settings.get("branding", {}),
        seo=tenant.settings.get("seo", {}),
        fulfillment=tenant.settings.get("fulfillment", {}),
    )
