from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import DbSession, require_tenant_scopes
from app.core.scopes import Scope
from app.tenancy.context import TenantContext
from app.tenancy.models import DomainPurpose
from app.tenancy.repository import TenantRepository

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel do tenant"])


class TenantPanelContext(BaseModel):
    tenant_id: str
    slug: str
    name: str
    status: str
    timezone: str
    currency: str
    primary_host: str | None
    features: dict[str, bool]
    settings: dict[str, dict[str, object]]


@router.get(
    "/context",
    response_model=TenantPanelContext,
    summary="Contexto do tenant para o painel (exige membership)",
)
async def panel_context(
    session: DbSession,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_READ))],
) -> TenantPanelContext:
    primary = await TenantRepository(session).primary_domain(tenant.id, DomainPurpose.STOREFRONT)
    return TenantPanelContext(
        tenant_id=tenant.id,
        slug=tenant.slug,
        name=tenant.name,
        status=tenant.status,
        timezone=tenant.timezone,
        currency=tenant.currency,
        primary_host=primary.hostname if primary else None,
        features=tenant.features,
        settings=tenant.settings,
    )
