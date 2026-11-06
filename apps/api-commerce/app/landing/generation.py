"""Da fila até a proposta pronta — agora a loja inteira, não só a página inicial.

O desenho todo existe para uma frase: **nunca segurar transação durante a chamada de rede.** Uma
geração leva dezenas de segundos; uma transação aberta esse tempo é uma conexão do pool
inutilizada, linhas travadas para quem tentar editar, e uma reciclagem do pool no meio derruba
tudo. Então o trabalho é fatiado assim, e é o mesmo recorte de `process_media`:

    tx#1  toma o rascunho (queued → running) e congela brief, inventário, o que está no ar,
          o perfil gravado e a lista de material. COMMIT E FECHA.
    ----  lê as miniaturas do storage e mede as cores (Pillow, fora do laço de eventos).
    ----  perfil da marca: reaproveita o gravado, ou chama a visão só com o material novo; se a
          visão falhar, monta o perfil sem modelo. Nenhuma transação aberta.
    tx#2  grava o perfil (e a paleta que faltava nos logotipos).
    ----  compõe a proposta (marca + áreas + blocos) e valida; uma rodada de correção.
    tx#3  confere as referências contra o banco de agora.
    tx#4  grava a proposta pronta (ou a falha, devolvendo a cota).

A cota já foi reservada na hora do pedido, **antes** de qualquer gasto — ver `quota.py`. Uma
proposta é uma unidade, com ou sem leitura de visão: a lojista pediu uma vez.

**O modelo nunca escreve na loja.** O que sai daqui vira `landing_drafts`; publicar é a
lojista apertando um botão, pela mesma porta da edição à mão (`drafts.py`).

**O perfil falhar não derruba a proposta.** Visão indisponível, resposta ilegível, feature que a
api-agents ainda não conhece (a janela entre os dois deploys): o perfil sai do Pillow e do brief,
com confiança baixa e dito na explicação. A composição falhar, sim, derruba — sem ela não há o
que mostrar.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Any, Protocol

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import TenantNotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.storage import ObjectStorage
from app.landing.brand import (
    BrandProfileV1,
    BrandReply,
    Material,
    RefreshPlan,
    brief_digest,
    deterministic_materials,
    deterministic_profile,
    logo_media_ids,
    merge_materials,
    parse_profile,
    plan_refresh,
)
from app.landing.brief import parse_brief
from app.landing.design_rules import (
    Normalized,
    check_brand_fidelity,
    describe_changes,
    effective_branding,
    enforce_brand_fidelity,
    filter_decisions,
    normalize,
    strip_logo_scenes,
)
from app.landing.gateway import (
    FEATURE_BRAND,
    FEATURE_DRAFT,
    FEATURE_REFINE,
    LlmGateway,
    LlmUnavailableError,
)
from app.landing.inventory import collect_inventory, with_analysis
from app.landing.materials import collect_materials, load_thumbnails, save_palettes
from app.landing.models import BrandProfile, DraftSource, DraftStatus, LandingBrief, LandingDraft
from app.landing.prompt import build_brand_prompt, build_compose_prompt, build_refine_prompt
from app.landing.quota import LandingQuotaService
from app.landing.repair import UnparseableReplyError, parse_json_object, repair_blocks
from app.landing.schema_export import brand_reply_schema, proposal_generation_schema
from app.landing.schemas import BriefV1
from app.models.base import utcnow
from app.tenancy.context import bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.setting_refs import check_setting_references
from app.tenancy.settings_schemas import validate_setting

logger = get_logger(__name__)

#: Temperatura da composição. Baixa o bastante para o modelo seguir o esquema, alta o bastante
#: para não sair a mesma página para toda padaria do Brasil.
TEMPERATURE = 0.7
#: A leitura da marca descreve o que vê: quanto menos criatividade, melhor.
BRAND_TEMPERATURE = 0.2
#: Teto de saída da leitura da marca (perfil + uma análise curta por arquivo).
BRAND_MAX_OUTPUT_TOKENS = 3000
#: As chaves de `tenant_settings` que uma proposta lê e publica.
PROPOSAL_KEYS = ("branding", "design", "landing")


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
    parent: dict[str, Any] | None
    quota_period: str
    current: dict[str, dict[str, Any]]
    materials: list[Material]
    stored_profile: BrandProfileV1 | None
    stored_materials: dict[str, dict[str, Any]]
    stored_digest: str | None
    stored_source: str | None


@dataclass(slots=True)
class _Profile:
    """O perfil desta proposta e como ele foi obtido."""

    profile: BrandProfileV1
    materials: dict[str, dict[str, Any]]
    source: str
    refreshed: bool
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    changes: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    pending: int = 0
    note: str | None = None


@dataclass(slots=True)
class _Spend:
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 0


async def generate_draft(
    tx: TxRunner,
    gateway: LlmGateway,
    *,
    tenant_id: str,
    draft_id: str,
    storage: ObjectStorage | None = None,
) -> str:
    """Monta uma proposta. Devolve o status final, que é o que o log do worker mostra."""

    claimed = await tx(partial(_claim, tenant_id=tenant_id, draft_id=draft_id))
    if isinstance(claimed, str):
        # Já estava rodando, já terminou, ou sumiu: nada a fazer, e nenhuma cota a devolver.
        return claimed

    perfil = await _profile_step(tx, gateway, storage, claimed, tenant_id=tenant_id)
    gasto = _Spend(
        prompt_tokens=perfil.prompt_tokens,
        completion_tokens=perfil.completion_tokens,
        latency_ms=perfil.latency_ms,
    )
    inventario = with_analysis(
        claimed.inventory, analysis=perfil.materials, materials=claimed.materials
    )
    logos = logo_media_ids(perfil.materials)

    schema = proposal_generation_schema()
    feedback: list[dict[str, str]] | None = None
    ultimo_erro = "Não foi possível montar a proposta."
    reparado = False

    for tentativa in range(settings.landing_llm_max_attempts):
        ultima = tentativa == settings.landing_llm_max_attempts - 1
        gasto.attempts = tentativa + 1
        if claimed.source == DraftSource.REFINE and claimed.parent is not None:
            messages = build_refine_prompt(
                proposal=claimed.parent,
                instruction=claimed.instruction or "",
                inventory=inventario,
                profile=perfil.profile,
                feedback=feedback,
            )
        else:
            messages = build_compose_prompt(
                brief=claimed.brief,
                inventory=inventario,
                profile=perfil.profile,
                current=claimed.current,
                profile_changes=perfil.changes,
                feedback=feedback,
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

        # Somados entre as tentativas e com a leitura da marca: a loja pediu uma vez, e o que
        # custou foi tudo isso.
        gasto.model = reply.model
        gasto.prompt_tokens += reply.prompt_tokens
        gasto.completion_tokens += reply.completion_tokens
        gasto.latency_ms += reply.latency_ms

        try:
            bruto = parse_json_object(reply.text)
        except UnparseableReplyError as exc:
            ultimo_erro = f"A resposta do modelo não veio no formato esperado ({exc})."
            feedback = [{"field": "resposta", "message": "devolva só o objeto JSON pedido"}]
            continue

        if claimed.source == DraftSource.REFINE and claimed.parent is not None:
            # Refino que só mexe nos blocos devolve só os blocos: o resto da proposta fica.
            bruto = {**claimed.parent, **bruto}
        proposta = normalize(
            bruto,
            current_branding=claimed.current["branding"],
            current_design=claimed.current["design"],
        )
        check_brand_fidelity(
            proposta, profile=perfil.profile, current_branding=claimed.current["branding"]
        )
        if proposta.errors and not ultima:
            ultimo_erro = "A proposta não usou a identidade da marca."
            feedback = [f.as_feedback() for f in proposta.errors]
            continue
        enforce_brand_fidelity(proposta, profile=perfil.profile)
        reparado = reparado or proposta.repaired

        blocos, mexeu = repair_blocks(proposta.blocks, inventory=inventario)
        blocos, tirou_logo = strip_logo_scenes(blocos, logo_ids=logos)
        reparado = reparado or mexeu or tirou_logo

        try:
            _, valor = validate_setting("landing", {"blocks": blocos})
        except ValidationError as exc:
            ultimo_erro = "A proposta não passou na validação."
            feedback = _feedback(exc)
            continue
        blocos = list(valor.get("blocks") or [])

        # tx#3: as referências contra o banco de agora. Um produto pode ter sido despublicado
        # enquanto o modelo escrevia.
        desconhecidos = await tx(partial(_unknown_refs, tenant_id=tenant_id, blocks=blocos))
        if desconhecidos:
            # Nós sabemos exatamente quais ids não existem: consertar é de graça.
            blocos, _ = repair_blocks(blocos, inventory=inventario, unknown_ids=desconhecidos)
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
            feedback = [{"field": "blocks", "message": "devolva de 4 a 8 blocos"}]
            continue

        try:
            validate_setting(
                "branding", effective_branding(claimed.current["branding"], proposta.branding)
            )
            validate_setting("design", proposta.design)
        except ValidationError as exc:  # pragma: no cover - `normalize` já filtrou campo a campo
            ultimo_erro = "A marca proposta não passou na validação."
            feedback = _feedback(exc)
            continue

        await tx(
            partial(
                _ready,
                tenant_id=tenant_id,
                draft_id=draft_id,
                proposta=proposta,
                blocks=blocos,
                summary=_summary(proposta, blocos, claimed, perfil),
                profile_snapshot=perfil.profile.model_dump(mode="json"),
                spend=gasto,
                repaired=reparado,
            )
        )
        return str(DraftStatus.READY)

    return await _fail(tx, tenant_id, draft_id, ultimo_erro, claimed.quota_period)


# ------------------------------------------------------------------ perfil da marca


async def _profile_step(
    tx: TxRunner,
    gateway: LlmGateway,
    storage: ObjectStorage | None,
    claimed: _Claim,
    *,
    tenant_id: str,
) -> _Profile:
    """O perfil desta proposta: do cache, da visão (só o material novo) ou do Pillow."""
    digest = brief_digest(claimed.brief)
    if claimed.source == DraftSource.REFINE:
        # Refino reescreve a partir de uma frase: reler a marca seria gastar visão à toa.
        plano = RefreshPlan(reuse=claimed.stored_profile is not None)
    else:
        plano = plan_refresh(
            stored_materials=claimed.stored_materials,
            materials=claimed.materials,
            digest=digest,
            stored_digest=claimed.stored_digest,
            has_profile=claimed.stored_profile is not None,
        )

    materiais, data_urls, paletas = await load_thumbnails(
        storage, claimed.materials, send=set(plano.analyse)
    )
    if plano.reuse and claimed.stored_profile is not None:
        if paletas:
            await tx(partial(_persist_palettes, tenant_id=tenant_id, palettes=paletas))
        return _Profile(
            profile=claimed.stored_profile,
            materials=claimed.stored_materials,
            source=claimed.stored_source or "deterministic",
            refreshed=False,
        )

    agora = utcnow().isoformat()
    semente = deterministic_profile(
        claimed.brief, materials=materiais, previous=claimed.stored_profile
    )
    enviar = [mid for mid in plano.analyse if mid in data_urls]
    resultado: _Profile | None = None
    if enviar:
        resultado = await _read_brand(
            gateway,
            claimed,
            materiais=materiais,
            enviar=enviar,
            data_urls=data_urls,
            semente=semente,
            removidos=plano.removed,
            tenant_id=tenant_id,
            analysed_at=agora,
        )

    if resultado is None:
        # Sem imagem para mandar, ou a visão falhou: o que der para saber sem modelo. Um perfil
        # que o modelo já leu continua valendo — o Pillow não sabe mais que ele.
        leitura = deterministic_materials(
            claimed.stored_materials,
            materials=materiais,
            analyse=plano.analyse,
            removed=plano.removed,
            analysed_at=agora,
        )
        anterior_lido = claimed.stored_profile is not None and claimed.stored_source == "llm"
        resultado = _Profile(
            profile=claimed.stored_profile if anterior_lido else semente,  # type: ignore[arg-type]
            materials=leitura,
            source="llm" if anterior_lido else "deterministic",
            refreshed=True,
            note=(
                "A leitura das imagens não respondeu; usamos as cores medidas no logotipo."
                if enviar
                else None
            ),
        )
    resultado.pending = len(plano.pending)

    await tx(
        partial(
            _persist_profile,
            tenant_id=tenant_id,
            perfil=resultado,
            digest=digest,
            palettes=paletas,
        )
    )
    return resultado


async def _read_brand(
    gateway: LlmGateway,
    claimed: _Claim,
    *,
    materiais: list[Material],
    enviar: list[str],
    data_urls: dict[str, str],
    semente: BrandProfileV1,
    removidos: list[str],
    tenant_id: str,
    analysed_at: str,
) -> _Profile | None:
    """A chamada de visão. `None` quando não deu — quem chama cai no perfil sem modelo."""
    schema = brand_reply_schema()
    feedback: list[dict[str, str]] | None = None
    gasto = _Spend()
    for _ in range(2):
        messages = build_brand_prompt(
            store_name=str(claimed.inventory.get("store_name") or "a loja"),
            brief=claimed.brief,
            materials=materiais,
            send=enviar,
            data_urls=data_urls,
            previous=claimed.stored_profile,
            seed=semente,
            removed=removidos,
            feedback=feedback,
        )
        try:
            reply = await gateway.complete(
                feature=FEATURE_BRAND,
                messages=messages,
                response_json_schema=schema,
                tenant_ref=tenant_id,
                max_output_tokens=BRAND_MAX_OUTPUT_TOKENS,
                temperature=BRAND_TEMPERATURE,
            )
        except LlmUnavailableError as exc:
            logger.warning(
                "brand reading unavailable", extra={"tenant_id": tenant_id, "reason": exc.message}
            )
            return None
        gasto.model = reply.model
        gasto.prompt_tokens += reply.prompt_tokens
        gasto.completion_tokens += reply.completion_tokens
        gasto.latency_ms += reply.latency_ms
        try:
            leitura = BrandReply.model_validate(parse_json_object(reply.text))
        except (UnparseableReplyError, PydanticValidationError) as exc:
            feedback = [{"field": "resposta", "message": f"JSON fora do formato: {str(exc)[:160]}"}]
            continue

        conhecidos = set(enviar)
        analises = [a for a in leitura.materials if a.media_id in conhecidos]
        mapa = merge_materials(
            claimed.stored_materials,
            materials=materiais,
            analyses=analises,
            removed=removidos,
            analysed_at=analysed_at,
        )
        # Arquivo que o modelo pulou fica com a leitura sem modelo, e não some do mapa: senão
        # ele seria "novo" para sempre e iria à visão em toda proposta.
        faltaram = [mid for mid in enviar if mid not in {a.media_id for a in analises}]
        if faltaram:
            mapa = deterministic_materials(
                mapa, materials=materiais, analyse=faltaram, removed=(), analysed_at=analysed_at
            )
        return _Profile(
            profile=leitura.profile,
            materials=mapa,
            source="llm",
            refreshed=True,
            model=gasto.model,
            prompt_tokens=gasto.prompt_tokens,
            completion_tokens=gasto.completion_tokens,
            latency_ms=gasto.latency_ms,
            changes=list(leitura.changes),
            contradictions=list(leitura.contradictions),
        )
    return None


# ------------------------------------------------------------------ as transações


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
    materials = await collect_materials(session)
    perfil = (await session.execute(select(BrandProfile))).scalar_one_or_none()

    parent: dict[str, Any] | None = None
    if draft.parent_draft_id:
        anterior = await session.get(LandingDraft, draft.parent_draft_id)
        if anterior is not None and anterior.blocks:
            parent = {
                "summary": (anterior.summary or {}).get("text", ""),
                "branding": dict(anterior.branding or {}),
                "design": dict(anterior.design or context.settings.get("design") or {}),
                "blocks": list(anterior.blocks),
            }

    current = {key: dict(context.settings.get(key) or {}) for key in PROPOSAL_KEYS}

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
        parent=parent,
        quota_period=draft.quota_period,
        current=current,
        materials=materials,
        stored_profile=parse_profile(perfil.data) if perfil else None,
        stored_materials=dict(perfil.materials or {}) if perfil else {},
        stored_digest=perfil.brief_digest if perfil else None,
        stored_source=perfil.source if perfil else None,
    )


async def _persist_profile(
    session: AsyncSession,
    *,
    tenant_id: str,
    perfil: _Profile,
    digest: str,
    palettes: dict[str, dict[str, Any]],
) -> None:
    """tx#2: o perfil revisto e a paleta que faltava nos logotipos."""
    bind_session_tenant(session, tenant_id)
    await save_palettes(session, palettes)
    linha = (await session.execute(select(BrandProfile))).scalar_one_or_none()
    if linha is None:
        linha = BrandProfile(
            tenant_id=tenant_id,
            version=0,
            created_by_actor="system:landing",
            updated_by_actor="system:landing",
        )
        session.add(linha)
    linha.data = perfil.profile.model_dump(mode="json")
    linha.materials = perfil.materials
    linha.brief_digest = digest
    linha.version = (linha.version or 0) + 1
    linha.source = perfil.source
    linha.model = perfil.model or linha.model
    linha.prompt_tokens = perfil.prompt_tokens
    linha.completion_tokens = perfil.completion_tokens
    linha.changes = {
        "changes": perfil.changes,
        "contradictions": perfil.contradictions,
        "pending": perfil.pending,
    }
    linha.updated_by_actor = "system:landing"
    await session.flush()


async def _persist_palettes(
    session: AsyncSession, *, tenant_id: str, palettes: dict[str, dict[str, Any]]
) -> None:
    bind_session_tenant(session, tenant_id)
    await save_palettes(session, palettes)


async def _unknown_refs(
    session: AsyncSession, *, tenant_id: str, blocks: list[dict[str, Any]]
) -> set[str]:
    """tx#3: quais ids da proposta não são linha desta loja."""
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
    proposta: Normalized,
    blocks: list[dict[str, Any]],
    summary: dict[str, Any],
    profile_snapshot: dict[str, Any],
    spend: _Spend,
    repaired: bool,
) -> None:
    """tx#4: a proposta pronta, esperando a lojista decidir."""
    bind_session_tenant(session, tenant_id)
    draft = await session.get(LandingDraft, draft_id)
    if draft is None:  # pragma: no cover - só se alguém apagar no meio
        return
    draft.status = DraftStatus.READY
    draft.blocks = blocks
    draft.branding = proposta.branding
    draft.design = proposta.design
    draft.summary = summary
    draft.profile_snapshot = profile_snapshot
    draft.model = spend.model
    draft.prompt_tokens = spend.prompt_tokens
    draft.completion_tokens = spend.completion_tokens
    draft.latency_ms = spend.latency_ms
    draft.attempts = spend.attempts
    draft.repaired = repaired
    draft.failure_reason = None
    await _prune(session, keep=settings.landing_drafts_kept)


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


def _summary(
    proposta: Normalized, blocos: list[dict[str, Any]], claimed: _Claim, perfil: _Profile
) -> dict[str, Any]:
    """O que a lojista lê, montado a partir dos valores — nunca só do texto do modelo."""
    mudancas = describe_changes(
        current=claimed.current,
        branding=proposta.branding,
        design=proposta.design,
        blocks=blocos,
    )
    return {
        "text": proposta.summary,
        "decisions": filter_decisions(proposta.decisions, mudancas),
        "changes": mudancas,
        "fixes": [f.message for f in proposta.findings if f.severity == "fix"][:8],
        "profile": {
            "source": perfil.source,
            "refreshed": perfil.refreshed,
            "changes": perfil.changes[:8],
            "contradictions": perfil.contradictions[:5],
            "pending": perfil.pending,
            "note": perfil.note,
        },
    }


async def _brief_of(session: AsyncSession) -> BriefV1:
    row = (await session.execute(select(LandingBrief))).scalar_one_or_none()
    return parse_brief(row.data) if row else BriefV1()


def _feedback(error: ValidationError) -> list[dict[str, str]]:
    """Os erros da validação no formato que o prompt sabe ler."""
    erros = error.details.get("errors")
    if isinstance(erros, list):
        return [
            {"field": str(e.get("field", "")), "message": str(e.get("message", ""))}
            for e in erros
            if isinstance(e, dict)
        ][:12]
    return [{"field": "blocks", "message": str(error.message)}]


#: Quantas propostas publicadas ficam guardadas além da poda comum: são o "restaurar".
KEEP_APPLIED = 3


async def _prune(session: AsyncSession, *, keep: int) -> None:
    """Guarda as últimas `keep` propostas resolvidas da loja e apaga o resto — menos as últimas
    publicadas, que carregam o que estava no ar antes delas.

    `queued` e `running` ficam de fora: apagar uma proposta que o worker está montando deixaria
    a cota sem quem a devolva.
    """
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
                            str(DraftStatus.REVERTED),
                        ]
                    )
                )
                .order_by(LandingDraft.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    publicadas = [r.id for r in rows if r.status in {DraftStatus.APPLIED, DraftStatus.REVERTED}]
    poupadas = set(publicadas[:KEEP_APPLIED])
    for row in rows[keep:]:
        if row.id in poupadas:
            continue
        await session.delete(row)
