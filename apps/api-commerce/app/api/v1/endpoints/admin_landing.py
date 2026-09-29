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

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.api.v1.endpoints.storefront_catalog import _card, _category
from app.catalog.storefront import image_payload
from app.core.scopes import Scope
from app.landing.brief import STEP_KEYS, LandingBriefService, filled_steps
from app.landing.quota import LandingQuotaService
from app.landing.resolver import resolve_landing
from app.landing.schemas import BriefV1
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


# ---------------------------------------------------------------- brief da loja


class QuotaOut(BaseModel):
    period: str
    used: int
    limit: int
    left: int
    #: Módulo `landing_ai` contratado. Falso não impede gerar — só aponta para o teto pequeno.
    paid: bool


class BriefStateOut(BaseModel):
    """O questionário e o que a tela precisa desenhar em volta dele."""

    brief: BriefV1
    #: Passos com alguma resposta, na ordem da tela. Vira o "2 de 4" do selo de progresso.
    steps: list[str]
    #: Todos os passos, para a tela não repetir a lista dela.
    all_steps: list[str]
    #: Dá para gerar alguma coisa? Sem dizer o que a loja vende, não há página a escrever.
    usable: bool
    #: Quantas propostas restam **neste mês**. Vai acima do botão, nunca depois de clicar.
    quota: QuotaOut


async def _state(session: DbSession, tenant: TenantContext) -> BriefStateOut:
    brief = await LandingBriefService(session, tenant).read()
    cota = await LandingQuotaService(session, tenant).state()
    return BriefStateOut(
        brief=brief,
        steps=list(filled_steps(brief)),
        all_steps=list(STEP_KEYS),
        usable=brief.usable,
        quota=QuotaOut(
            period=cota.period,
            used=cota.used,
            limit=cota.limit,
            left=cota.left,
            paid=cota.paid,
        ),
    )


@router.get(
    "/landing/brief",
    response_model=BriefStateOut,
    summary="O que a loja já contou sobre si",
)
async def get_brief(
    session: DbSession,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
) -> BriefStateOut:
    return await _state(session, tenant)


@router.patch(
    "/landing/brief",
    response_model=BriefStateOut,
    summary="Salva um passo do questionário (só o que vier no corpo)",
)
async def patch_brief(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
    body: BriefV1,
) -> BriefStateOut:
    """Grava o que chegou e devolve o estado inteiro.

    `exclude_unset` é o que faz o passo 2 não apagar o passo 1: chave ausente não mexe no campo,
    `null` explícito limpa. Sem isso, cada tela mandaria o brief completo — e a que estivesse com
    dado velho na mão venceria a outra.
    """
    await LandingBriefService(session, tenant).patch(
        body.model_dump(mode="json", exclude_unset=True), admin_actor(request, user)
    )
    await session.commit()
    return await _state(session, tenant)
