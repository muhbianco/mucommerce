"""Prévia da página inicial, para o painel.

Existe porque o painel não pode abrir a vitrine num iframe: o CSP manda `frame-ancestors 'none'`
em tudo, e abrir exceção cobriria só o endereço da plataforma — não o domínio próprio de cada
loja.

O que ele devolve é o que `GET /storefront/landing` devolveria, pelo **mesmo resolver**. Um
resolver, duas portas: a prévia não diverge do que o cliente vê por construção. Se divergisse,
o lojista publicaria confiando numa tela que mente.

Uma diferença, e é de propósito: aqui quem olha é o dono, então os blocos de catálogo vêm
cheios mesmo numa loja que pede cadastro. Ele precisa ver a própria página, não a versão que o
visitante de fora recebe.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_tenant_scopes
from app.api.v1.endpoints.storefront_catalog import _card, _category
from app.catalog.storefront import image_payload
from app.core.scopes import Scope
from app.landing.resolver import resolve_landing
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Vitrine"])


@router.get(
    "/landing/preview",
    response_model=list[dict[str, Any]],
    summary="A página inicial como a vitrine vai desenhá-la",
)
async def landing_preview(
    session: DbSession,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
) -> list[dict[str, Any]]:
    return await resolve_landing(
        session,
        tenant,
        show_catalog=True,
        card_payload=lambda card, currency: _card(card, currency).model_dump(mode="json"),
        category_payload=lambda category: _category(category).model_dump(),
        image_payload=image_payload,
    )
