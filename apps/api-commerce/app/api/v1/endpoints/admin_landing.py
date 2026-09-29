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
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.api.v1.endpoints.storefront_catalog import _card, _category
from app.catalog.storefront import image_payload
from app.core.config import settings
from app.core.scopes import Scope
from app.landing.brief import STEP_KEYS, LandingBriefService, filled_steps
from app.landing.drafts import LandingDraftService
from app.landing.models import LandingDraft
from app.landing.quota import LandingQuotaService
from app.landing.resolver import resolve_landing
from app.landing.schemas import BriefV1
from app.media.models import MediaOwner, MediaStatus
from app.media.repository import MediaRepository
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


# ---------------------------------------------------------------- propostas de vitrine


class DraftOut(BaseModel):
    """Uma proposta, como a tela precisa dela.

    Sem o prompt e sem a resposta crua: são caros de guardar, não ajudam a lojista e o que a
    gente registra de uma chamada ao modelo é modelo, tokens, latência e situação — nunca o
    corpo.
    """

    id: str
    status: str
    source: str
    parent_draft_id: str | None
    instruction: str | None
    #: Nulo enquanto a montagem não terminou.
    blocks: list[dict[str, Any]] | None
    #: A saída precisou de conserto determinístico (id inventado, lista comprida demais).
    repaired: bool
    failure_reason: str | None
    created_at: str
    applied_at: str | None


def _draft_out(draft: LandingDraft) -> DraftOut:
    return DraftOut(
        id=draft.id,
        status=str(draft.status),
        source=str(draft.source),
        parent_draft_id=draft.parent_draft_id,
        instruction=draft.instruction,
        blocks=draft.blocks,
        repaired=draft.repaired,
        failure_reason=draft.failure_reason,
        created_at=draft.created_at.isoformat(),
        applied_at=draft.applied_at.isoformat() if draft.applied_at else None,
    )


class DraftListOut(BaseModel):
    drafts: list[DraftOut]
    quota: QuotaOut
    #: A montagem com IA está ligada nesta instalação. Desligada, a tela esconde o botão em vez
    #: de oferecer algo que vai falhar.
    enabled: bool


class RefineIn(BaseModel):
    instruction: Annotated[str, Field(min_length=1, max_length=200)]


@router.get(
    "/landing/drafts",
    response_model=DraftListOut,
    summary="Propostas de página inicial desta loja",
)
async def list_drafts(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
) -> DraftListOut:
    service = LandingDraftService(session, tenant, admin_actor(request, user))
    cota = await LandingQuotaService(session, tenant).state()
    return DraftListOut(
        drafts=[_draft_out(d) for d in await service.list()],
        quota=QuotaOut(
            period=cota.period, used=cota.used, limit=cota.limit, left=cota.left, paid=cota.paid
        ),
        enabled=settings.landing_llm_enabled,
    )


@router.post(
    "/landing/drafts",
    response_model=DraftOut,
    status_code=202,
    summary="Pede uma proposta a partir do brief (consome uma unidade)",
)
async def request_draft(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
) -> DraftOut:
    """202: a montagem roda no worker. A tela consulta a lista até sair de `queued`/`running`."""
    draft = await LandingDraftService(session, tenant, admin_actor(request, user)).request()
    await session.commit()
    return _draft_out(draft)


@router.post(
    "/landing/drafts/{draft_id}/refine",
    response_model=DraftOut,
    status_code=202,
    summary="Reescreve uma proposta a partir de uma instrução curta (consome uma unidade)",
)
async def refine_draft(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
    draft_id: str,
    body: RefineIn,
) -> DraftOut:
    service = LandingDraftService(session, tenant, admin_actor(request, user))
    draft = await service.refine(draft_id, body.instruction)
    await session.commit()
    return _draft_out(draft)


@router.post(
    "/landing/drafts/{draft_id}/apply",
    response_model=dict[str, Any],
    summary="Publica a proposta (mesma porta da edição à mão)",
)
async def apply_draft(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
    draft_id: str,
) -> dict[str, Any]:
    service = LandingDraftService(session, tenant, admin_actor(request, user))
    saved = await service.apply(draft_id)
    await session.commit()
    return saved


@router.post(
    "/landing/drafts/{draft_id}/discard",
    response_model=DraftOut,
    summary="Descarta a proposta",
)
async def discard_draft(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
    draft_id: str,
) -> DraftOut:
    service = LandingDraftService(session, tenant, admin_actor(request, user))
    draft = await service.discard(draft_id)
    await session.commit()
    return _draft_out(draft)


@router.get(
    "/landing/drafts/{draft_id}/preview",
    response_model=list[dict[str, Any]],
    summary="A proposta como a vitrine vai desenhá-la",
)
async def draft_preview(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
    draft_id: str,
) -> list[dict[str, Any]]:
    """Pelo **mesmo resolver** da vitrine, com os blocos da proposta no lugar dos salvos.

    A prévia vive no painel, e não numa rota da loja, por três razões: uma rota de rascunho na
    vitrine teria chave de cache que nunca pode ser compartilhada, precisaria de sessão de admin
    num host que não tem, e abriria um caminho para página não publicada vazar.
    """
    service = LandingDraftService(session, tenant, admin_actor(request, user))
    draft = await service.get_or_404(draft_id)
    return await resolve_landing(
        session,
        tenant,
        show_catalog=True,
        blocks_override=draft.blocks or [],
        card_payload=lambda card, currency: _card(card, currency).model_dump(mode="json"),
        category_payload=lambda category: _category(category).model_dump(),
        image_payload=image_payload,
    )


# ---------------------------------------------------------------- cores do logotipo


class PaletteOut(BaseModel):
    """Uma sugestão de cor, extraída de uma imagem da marca."""

    media_id: str
    #: A miniatura, para a lojista ver de qual imagem veio a sugestão.
    thumbnail_url: str | None
    primary: str
    #: Preto ou branco, o que lê melhor sobre `primary`. Calculado, nunca escolhido.
    on_primary: str
    secondary: str | None


@router.get(
    "/branding/suggestions",
    response_model=list[PaletteOut],
    summary="Cores sugeridas a partir das imagens da marca",
)
async def branding_suggestions(
    session: DbSession,
    tenant: Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))],
) -> list[PaletteOut]:
    """As cores que já foram extraídas de cada logotipo, no momento do processamento.

    Extrair aqui seria baixar a imagem do storage a cada abertura da tela para calcular sempre a
    mesma coisa. A paleta é propriedade do arquivo, não do momento: fica gravada em
    `media_assets.palette` quando a imagem é processada.

    **Isto não escreve `branding`.** Sugerir e aplicar são coisas diferentes: a lojista olha as
    amostras e decide. Uma loja que já escolheu a cor dela não pode perdê-la porque trocou o
    logotipo.
    """
    rows = await MediaRepository(session).for_owner(MediaOwner.TENANT_BRAND, None)
    saidas: list[PaletteOut] = []
    for media in rows:
        palette = media.palette or {}
        primary = palette.get("primary")
        if media.status != MediaStatus.READY or not isinstance(primary, str):
            continue
        renditions = image_payload(media) or {}
        variantes = renditions.get("renditions") or []
        secondary = palette.get("secondary")
        saidas.append(
            PaletteOut(
                media_id=media.id,
                thumbnail_url=str(variantes[0]["url"]) if variantes else None,
                primary=primary,
                on_primary=str(palette.get("on_primary") or "#ffffff"),
                secondary=secondary if isinstance(secondary, str) else None,
            )
        )
    return saidas
