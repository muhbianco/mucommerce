from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.scopes import Scope
from app.schemas.tenant import SettingUpdate
from app.tenancy.context import TenantContext
from app.tenancy.models import DomainPurpose
from app.tenancy.repository import TenantRepository
from app.tenancy.service import TenantService

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


# Keys the tenant edits in the panel. Access mode, fulfillment and checkout stay with MuhBianco
# ops for now (they change what customers can do, and phase 2 owns fulfillment/checkout).
# Day-to-day settings the store edits in its panel (platform ones stay in the site admin).
TenantEditableSetting = Literal["branding", "landing", "seo", "fulfillment", "checkout"]


@router.get(
    "/settings",
    response_model=dict[str, dict[str, Any]],
    summary="Configurações da loja (valores validados)",
)
async def get_settings(
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_READ))],
) -> dict[str, dict[str, Any]]:
    return tenant.settings


@router.put(
    "/settings/{key}",
    response_model=dict[str, Any],
    summary="Atualiza marca, landing, SEO, entrega ou checkout (validado por schema)",
)
async def put_setting(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
    key: TenantEditableSetting,
    body: SettingUpdate,
) -> dict[str, Any]:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    return await service.set_setting(row, key, body.value, admin_actor(request, user))
