from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import DbSession, StorefrontTenant
from app.schemas.internal import StorefrontContext
from app.tenancy.storefront_context import build_storefront_context

router = APIRouter(prefix="/storefront", tags=["Vitrine (público)"])


@router.get(
    "/context",
    response_model=StorefrontContext,
    summary="Contexto público do tenant resolvido pelo Host",
)
async def public_context(session: DbSession, tenant: StorefrontTenant) -> StorefrontContext:
    return await build_storefront_context(session, tenant, internal=False)
