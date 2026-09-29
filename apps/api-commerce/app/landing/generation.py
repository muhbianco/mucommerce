"""Da fila até a proposta pronta.

O desenho todo existe para uma frase: **nunca segurar transação durante a chamada de rede.** Uma
geração leva dezenas de segundos; uma transação aberta esse tempo é uma conexão do pool
inutilizada, linhas travadas para quem tentar editar, e uma reciclagem do pool no meio derruba
tudo. Então o trabalho é fatiado assim, e é o mesmo recorte de `process_media` e de
`PhoneVerificationService.start`:

    tx#1  toma o rascunho (queued → running) e congela brief + inventário. COMMIT E FECHA.
    ----  monta o prompt (função pura) e chama o gateway. Nenhuma transação aberta.
    tx#2  confere as referências contra o banco de agora.
    tx#3  grava a proposta pronta (ou a falha, devolvendo a cota).

A cota já foi reservada na hora do pedido, **antes** de qualquer gasto — ver `quota.py`. Aqui só
resta devolvê-la quando nada foi entregue. Isso faz a cota contar *entregas* e o livro-caixa do
outro lado contar *chamadas*: uma falha custa dinheiro e não custa cota. A divergência é
declarada, não acidental.

**O modelo nunca escreve na loja.** O que sai daqui vira `landing_drafts.blocks`; publicar é a
lojista apertando um botão, pela mesma porta da edição à mão.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import TenantNotFoundError, ValidationError
from app.core.logging import get_logger
from app.landing.brief import parse_brief
from app.landing.gateway import FEATURE_DRAFT, FEATURE_REFINE, LlmGateway, LlmUnavailableError
from app.landing.inventory import collect_inventory
from app.landing.models import DraftSource, DraftStatus, LandingBrief, LandingDraft
from app.landing.prompt import build_draft_prompt, build_refine_prompt
from app.landing.quota import LandingQuotaService
from app.landing.repair import UnparseableReplyError, parse_blocks, repair_blocks
from app.landing.schema_export import landing_generation_schema
from app.landing.schemas import BriefV1
from app.models.base import utcnow
from app.tenancy.context import bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.setting_refs import check_setting_references
from app.tenancy.settings_schemas import validate_setting

logger = get_logger(__name__)

#: Temperatura da montagem. Baixa o bastante para o modelo seguir o esquema, alta o bastante para
#: não sair a mesma página para toda padaria do Brasil.
TEMPERATURE = 0.7


class TxRunner(Protocol):
    """Roda `fn` na própria sessão e commita (o `with_session` do worker)."""

    def __call__[T](self, fn: Callable[[AsyncSession], Awaitable[T]]) -> Awaitable[T]: ...


@dataclass(frozen=True, slots=True)
class _Claim:
    """Tudo o que a parte sem transação precisa. Depois disto, nada mais é lido do banco."""

    source: str
    instruction: str | None
    brief: BriefV1
    inventory: dict[str, Any]
    parent_blocks: list[dict[str, Any]]
    quota_period: str


async def generate_draft(
    tx: TxRunner, gateway: LlmGateway, *, tenant_id: str, draft_id: str
) -> str:
    """Monta uma proposta. Devolve o status final, que é o que o log do worker mostra."""

    claimed = await tx(partial(_claim, tenant_id=tenant_id, draft_id=draft_id))
    if isinstance(claimed, str):
        # Já estava rodando, já terminou, ou sumiu: nada a fazer, e nenhuma cota a devolver.
        return claimed

    schema = landing_generation_schema()
    feedback: list[dict[str, str]] | None = None
    ultimo_erro = "Não foi possível montar a proposta."
    reparado = False
    tentativas = 0
    modelo = ""
    prompt_tokens = 0
    completion_tokens = 0
    latencia = 0

    for tentativa in range(settings.landing_llm_max_attempts):
        tentativas = tentativa + 1
        messages = (
            build_refine_prompt(
                blocks=claimed.parent_blocks,
                instruction=claimed.instruction or "",
                inventory=claimed.inventory,
                feedback=feedback,
            )
            if claimed.source == DraftSource.REFINE
            else build_draft_prompt(
                brief=claimed.brief, inventory=claimed.inventory, feedback=feedback
            )
        )

        try:
            reply = await gateway.complete(
                feature=(FEATURE_REFINE if claimed.source == DraftSource.REFINE else FEATURE_DRAFT),
                messages=messages,
                response_json_schema=schema,
                tenant_ref=tenant_id,
                external_ref=draft_id,
                max_output_tokens=settings.landing_llm_max_output_tokens,
                temperature=TEMPERATURE,
            )
        except LlmUnavailableError as exc:
            # Indisponibilidade não melhora com o mesmo pedido de novo, e uma segunda chamada
            # custaria de novo. Para aqui.
            return await _fail(tx, tenant_id, draft_id, str(exc.message), claimed.quota_period)

        modelo = reply.model
        # Somados entre as tentativas: a loja pediu uma vez, e o que custou foi tudo isso.
        prompt_tokens += reply.prompt_tokens
        completion_tokens += reply.completion_tokens
        latencia += reply.latency_ms

        try:
            blocos = parse_blocks(reply.text)
        except UnparseableReplyError as exc:
            ultimo_erro = f"A resposta do modelo não veio no formato esperado ({exc})."
            feedback = [{"field": "resposta", "message": "devolva só o objeto JSON pedido"}]
            continue

        blocos, mexeu = repair_blocks(blocos, inventory=claimed.inventory)
        reparado = reparado or mexeu

        try:
            _, valor = validate_setting("landing", {"blocks": blocos})
        except ValidationError as exc:
            ultimo_erro = "A proposta não passou na validação."
            feedback = _feedback(exc)
            continue

        blocos = list(valor.get("blocks") or [])
        # tx#2: as referências contra o banco de agora. Um produto pode ter sido despublicado
        # enquanto o modelo escrevia.
        desconhecidos = await tx(partial(_unknown_refs, tenant_id=tenant_id, blocks=blocos))
        if desconhecidos:
            # Nós sabemos exatamente quais ids não existem: consertar é de graça, e perguntar de
            # novo ao modelo pelo mesmo erro seria pagar por um conserto que sabemos fazer.
            blocos, _ = repair_blocks(
                blocos, inventory=claimed.inventory, unknown_ids=desconhecidos
            )
            reparado = True
            try:
                _, valor = validate_setting("landing", {"blocks": blocos})
            except ValidationError as exc:
                ultimo_erro = "A proposta apontava para itens que não existem."
                feedback = _feedback(exc)
                continue
            blocos = list(valor.get("blocks") or [])

        if not blocos:
            ultimo_erro = "A proposta veio vazia."
            feedback = [{"field": "blocks", "message": "devolva de 4 a 7 blocos"}]
            continue

        await tx(
            partial(
                _ready,
                tenant_id=tenant_id,
                draft_id=draft_id,
                blocks=blocos,
                model=modelo,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latencia,
                attempts=tentativas,
                repaired=reparado,
            )
        )
        return str(DraftStatus.READY)

    return await _fail(tx, tenant_id, draft_id, ultimo_erro, claimed.quota_period)


# ------------------------------------------------------------------ as três transações


async def _claim(session: AsyncSession, *, tenant_id: str, draft_id: str) -> _Claim | str:
    """tx#1: toma o rascunho e congela o que o modelo vai ver."""
    bind_session_tenant(session, tenant_id)
    draft = await session.get(LandingDraft, draft_id)
    if draft is None:
        return "missing"
    if draft.status != DraftStatus.QUEUED:
        return str(draft.status)

    try:
        context = await TenantResolver(session).resolve_by_id(tenant_id)
    except TenantNotFoundError:  # pragma: no cover - a loja sumiu entre o pedido e o worker
        return "missing"

    brief = await _brief_of(session)
    inventory = await collect_inventory(session, context)

    parent_blocks: list[dict[str, Any]] = []
    if draft.parent_draft_id:
        parent = await session.get(LandingDraft, draft.parent_draft_id)
        if parent is not None and parent.blocks:
            parent_blocks = list(parent.blocks)

    draft.status = DraftStatus.RUNNING
    draft.brief_snapshot = brief.model_dump(mode="json")
    draft.inventory_snapshot = inventory
    # Mexer no `updated_at` mantém o varredor de travados longe enquanto isto roda.
    draft.updated_at = utcnow()
    return _Claim(
        source=str(draft.source),
        instruction=draft.instruction,
        brief=brief,
        inventory=inventory,
        parent_blocks=parent_blocks,
        quota_period=draft.quota_period,
    )


async def _unknown_refs(
    session: AsyncSession, *, tenant_id: str, blocks: list[dict[str, Any]]
) -> set[str]:
    """tx#2: quais ids da proposta não são linha desta loja.

    `check_setting_references` devolve a lista nos detalhes do erro — é justamente por termos os
    ids que o reparo consegue ser determinístico.
    """
    bind_session_tenant(session, tenant_id)
    try:
        await check_setting_references(session, "landing", {"blocks": blocks})
    except ValidationError as exc:
        achados: set[str] = set()
        for campo in ("media_ids", "product_ids", "category_ids"):
            achados.update(str(x) for x in (exc.details.get(campo) or []))
        return achados
    return set()


async def _ready(
    session: AsyncSession,
    *,
    tenant_id: str,
    draft_id: str,
    blocks: list[dict[str, Any]],
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    attempts: int,
    repaired: bool,
) -> None:
    """tx#3: a proposta pronta, esperando a lojista decidir."""
    bind_session_tenant(session, tenant_id)
    draft = await session.get(LandingDraft, draft_id)
    if draft is None:  # pragma: no cover - só se alguém apagar no meio
        return
    draft.status = DraftStatus.READY
    draft.blocks = blocks
    draft.model = model
    draft.prompt_tokens = prompt_tokens
    draft.completion_tokens = completion_tokens
    draft.latency_ms = latency_ms
    draft.attempts = attempts
    draft.repaired = repaired
    draft.failure_reason = None
    await _prune(session, tenant_id, keep=settings.landing_drafts_kept)


async def _fail(tx: TxRunner, tenant_id: str, draft_id: str, reason: str, period: str) -> str:
    """Falha terminal: motivo legível **e** a cota de volta.

    A loja pediu uma vez e não recebeu nada; cobrar por isso seria cobrar pelo nosso erro.
    """

    async def work(session: AsyncSession) -> None:
        bind_session_tenant(session, tenant_id)
        draft = await session.get(LandingDraft, draft_id)
        if draft is None:  # pragma: no cover
            return
        draft.status = DraftStatus.FAILED
        draft.failure_reason = reason[:300]
        try:
            context = await TenantResolver(session).resolve_by_id(tenant_id)
        except TenantNotFoundError:  # pragma: no cover
            return
        await LandingQuotaService(session, context).release(period)

    await tx(work)
    logger.warning("landing draft failed", extra={"tenant_id": tenant_id, "draft_id": draft_id})
    return str(DraftStatus.FAILED)


# ------------------------------------------------------------------ apoio


async def _brief_of(session: AsyncSession) -> BriefV1:
    row = (await session.execute(select(LandingBrief))).scalar_one_or_none()
    return parse_brief(row.data) if row else BriefV1()


def _feedback(error: ValidationError) -> list[dict[str, str]]:
    """Os erros da validação no formato que o prompt sabe ler.

    `ValidationError` já carrega `[{field, message}]` — instrução de conserto legível por
    máquina, e a razão de uma segunda tentativa ter chance real de acertar.
    """
    erros = error.details.get("errors")
    if isinstance(erros, list):
        return [
            {"field": str(e.get("field", "")), "message": str(e.get("message", ""))}
            for e in erros
            if isinstance(e, dict)
        ][:12]
    return [{"field": "blocks", "message": str(error.message)}]


async def _prune(session: AsyncSession, tenant_id: str, *, keep: int) -> None:
    """Guarda as últimas `keep` propostas resolvidas da loja e apaga o resto.

    `queued` e `running` ficam de fora: apagar uma proposta que o worker está montando deixaria
    a cota sem quem a devolva.
    """
    del tenant_id
    rows = (
        (
            await session.execute(
                select(LandingDraft)
                .where(
                    LandingDraft.status.in_(
                        [
                            str(DraftStatus.READY),
                            str(DraftStatus.FAILED),
                            str(DraftStatus.APPLIED),
                            str(DraftStatus.DISCARDED),
                        ]
                    )
                )
                .order_by(LandingDraft.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows[keep:]:
        await session.delete(row)
