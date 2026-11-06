"""Pedir, olhar, publicar, restaurar e descartar propostas de desenho da loja.

A regra que organiza o arquivo inteiro: **publicar uma proposta é a mesma escrita que a edição à
mão.** `TenantService.set_setting` valida, confere referências e registra auditoria; um atalho
daqui direto para `tenant_settings` significaria dois caminhos de escrita e, mais cedo do que se
imagina, um deles esquecido numa mudança. Há uma regra em `test_architecture.py` cobrando isso.

**Publicar não é sem volta.** Antes de escrever, a proposta guarda o que estava no ar
(`previous_settings`); restaurar escreve isso de volta, pela mesma porta. A SGPipas publicou
uma proposta 48 minutos depois de editar a página à mão e perdeu a edição sem aviso e sem
caminho de volta (docs/16 §A.1).

**Uma versão no ar por vez.** Publicar põe a que estava no ar em `superseded` ("já esteve no
ar", com `replaced_at`), e qualquer versão que já esteve no ar, foi desfeita ou descartada pode
ser publicada de novo. Antes disto, "publicada" era uma marca que nunca saía, e a tela mostrava
três versões "no ar" ao mesmo tempo.

A ordem do pedido também é deliberada: **reserva a cota, depois enfileira.** Contar depois é como
se descobre, no fim do mês, que o limite nunca segurou nada.
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.core.config import settings
from app.core.exceptions import ConflictError, DomainError, NotFoundError, ValidationError
from app.landing.brief import parse_brief
from app.landing.design import AI_BRANDING_KEYS, DesignV1
from app.landing.design_rules import effective_branding
from app.landing.models import (
    KEEP_PUBLISHED,
    PUBLISHABLE_STATUSES,
    BrandProfile,
    DraftSource,
    DraftStatus,
    LandingBrief,
    LandingDraft,
)
from app.landing.quota import LandingQuotaService
from app.landing.repair import drop_unknown_refs
from app.landing.schemas import BriefV1
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.models import TenantSetting
from app.tenancy.service import Actor, TenantService
from app.tenancy.setting_refs import check_setting_references

#: Folga entre publicar e "editado à mão depois": a publicação grava as settings e só então
#: carimba `applied_at`, então a hora das settings fica alguns milissegundos antes.
EDIT_TOLERANCE_SECONDS = 5

#: As chaves que uma proposta publica (e que o restaurar devolve).
PROPOSAL_KEYS = ("branding", "design", "landing")

#: Quantas propostas a loja pode ter em pé ao mesmo tempo. Sem isto, clicar duas vezes no botão
#: gasta duas unidades e monta duas páginas quase iguais.
MAX_IN_FLIGHT = 2


class LandingAiDisabledError(DomainError):
    status_code = 503
    error_code = "landing_ai_disabled"
    message = "A montagem com IA ainda não está disponível."


class DraftAlreadyRunningError(ConflictError):
    error_code = "landing_draft_running"
    message = "Já há uma proposta sendo montada. Espere ela terminar."


class DraftNotReadyError(ConflictError):
    error_code = "landing_draft_not_ready"
    message = "Esta proposta ainda não está pronta."


class NotRevertibleError(ConflictError):
    error_code = "landing_draft_not_revertible"
    message = "Só dá para desfazer a versão que está no ar."


class NotPublishableError(ConflictError):
    error_code = "landing_draft_not_publishable"
    message = "Esta versão não pode ser publicada."


class NotDiscardableError(ConflictError):
    error_code = "landing_draft_not_discardable"
    message = "Só dá para descartar proposta que ainda não foi publicada."


class BriefTooEmptyError(ValidationError):
    error_code = "landing_brief_empty"
    message = "Conte pelo menos o que a sua loja vende antes de pedir uma proposta."


class LandingDraftService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.quota = LandingQuotaService(session, tenant)

    # ------------------------------------------------------------------ leitura

    async def list(self) -> list[LandingDraft]:
        stmt = (
            select(LandingDraft)
            .order_by(LandingDraft.created_at.desc())
            .limit(settings.landing_drafts_kept + MAX_IN_FLIGHT + KEEP_PUBLISHED)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_or_404(self, draft_id: str) -> LandingDraft:
        draft = await self.session.get(LandingDraft, draft_id)
        if draft is None:
            raise NotFoundError("Proposta não encontrada.")
        return draft

    # ------------------------------------------------------------------ pedir

    async def request(self) -> LandingDraft:
        """Uma proposta nova, a partir do brief."""
        brief = await self._brief()
        if not brief.usable:
            raise BriefTooEmptyError()
        return await self._enqueue(source=DraftSource.LLM)

    async def refine(self, parent_id: str, instruction: str) -> LandingDraft:
        """Reescrever uma proposta a partir de uma instrução curta.

        Custa **uma unidade inteira**, e não meia, embora o pedido seja menor: meia unidade é um
        chamado de suporte esperando acontecer, e o refino é um pedido como outro qualquer.

        Trocar imagem, reordenar bloco ou mudar arranjo **não passa por aqui** — o editor já faz
        isso de graça. "Troca a foto do destaque" não é trabalho de modelo de linguagem.
        """
        parent = await self.get_or_404(parent_id)
        if parent.status not in {DraftStatus.READY, DraftStatus.APPLIED} or not parent.blocks:
            raise DraftNotReadyError()
        texto = instruction.strip()
        if not texto:
            raise ValidationError("Diga o que você quer mudar.")
        return await self._enqueue(
            source=DraftSource.REFINE, parent_draft_id=parent.id, instruction=texto[:200]
        )

    async def _enqueue(
        self,
        *,
        source: str,
        parent_draft_id: str | None = None,
        instruction: str | None = None,
    ) -> LandingDraft:
        if not settings.landing_llm_enabled:
            raise LandingAiDisabledError()
        if await self._in_flight() >= MAX_IN_FLIGHT:
            raise DraftAlreadyRunningError()

        # A torneira antes do gasto. Estourou o mês, nada é enfileirado.
        state = await self.quota.reserve()

        draft = LandingDraft(
            tenant_id=self.tenant.id,
            status=DraftStatus.QUEUED,
            source=source,
            parent_draft_id=parent_draft_id,
            instruction=instruction,
            quota_period=state.period,
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(draft)
        await self.session.flush()
        # Pelo outbox, e não por `.delay` aqui: se a transação der rollback, a tarefa não pode
        # ter saído. É o mesmo caminho do processamento de imagem.
        await emit(
            self.session,
            aggregate_type="landing_draft",
            aggregate_id=draft.id,
            event_type="landing.draft_requested",
            payload={"draft_id": draft.id},
            tenant_id=self.tenant.id,
        )
        return draft

    async def _in_flight(self) -> int:
        stmt = select(LandingDraft.id).where(
            LandingDraft.status.in_([str(DraftStatus.QUEUED), str(DraftStatus.RUNNING)])
        )
        return len(list((await self.session.execute(stmt)).scalars().all()))

    # ------------------------------------------------------------------ decidir

    async def apply(self, draft_id: str) -> dict[str, Any]:
        """Publica uma versão: marca, desenho por área e página inicial, nesta ordem.

        Vale para a proposta nova (`ready`) e para publicar de novo uma versão que já esteve no
        ar, foi desfeita ou foi descartada. A que estava no ar vira `superseded` ("já esteve no
        ar") com `replaced_at`: uma versão no ar por vez.

        **Pela mesma porta da edição à mão**: `set_setting` valida, confere as referências
        contra as linhas desta loja e escreve a auditoria — uma linha por chave. O modelo nunca
        toca em `tenant_settings`. A marca é mesclada sobre a de agora, e só nas chaves que a IA
        pode mexer: um logotipo trocado depois da proposta continua o novo.

        Versão antiga pode citar produto arquivado ou foto apagada depois: esses ids saem antes
        de gravar (`drop_unknown_refs`), em vez de a publicação inteira ser recusada.

        Devolve o valor gravado da página inicial (o contrato antigo da rota).
        """
        draft = await self.get_or_404(draft_id)
        if draft.status in {DraftStatus.QUEUED, DraftStatus.RUNNING}:
            raise DraftNotReadyError()
        if draft.status not in PUBLISHABLE_STATUSES or not draft.blocks:
            raise NotPublishableError()

        service = TenantService(self.session)
        row = await service.get_or_404(self.tenant.id)
        atual = await service.repo.settings(self.tenant.id)
        no_ar = await self._live()
        anterior: dict[str, Any] = {key: atual.get(key) for key in PROPOSAL_KEYS}
        # Quem estava no ar: desfazer esta publicação a devolve ao ar, e a lista volta a dizer.
        anterior["live_draft_id"] = no_ar[0].id if no_ar else None

        blocos = list(draft.blocks)
        if draft.status != DraftStatus.READY:
            blocos = await self._without_stale_refs(blocos)

        if draft.branding:
            marca = effective_branding(dict(atual.get("branding") or {}), draft.branding)
            await service.set_setting(row, "branding", marca, self.actor)
        if draft.design:
            await service.set_setting(row, "design", dict(draft.design), self.actor)
        saved = await service.set_setting(
            row, "landing", {"blocks": _fresh_ids(blocos)}, self.actor
        )

        agora = utcnow()
        for outra in no_ar:
            if outra.id != draft.id:
                outra.status = DraftStatus.SUPERSEDED
                outra.replaced_at = agora
                outra.updated_by_actor = self.actor.id
        draft.previous_settings = anterior
        draft.status = DraftStatus.APPLIED
        draft.applied_at = agora
        draft.replaced_at = None
        draft.updated_by_actor = self.actor.id
        await self.session.flush()
        return saved

    async def revert(self, draft_id: str) -> LandingDraft:
        """Tira do ar a versão publicada e devolve o que estava antes dela.

        Só a que está no ar. A marca volta só nas chaves da IA (o logotipo atual fica); desenho
        e página voltam inteiros; chave que não existia antes volta ao padrão. Se antes dela havia
        outra versão no ar, essa volta a ser a "no ar" da lista.
        """
        draft = await self.get_or_404(draft_id)
        if draft.status != DraftStatus.APPLIED or draft.previous_settings is None:
            raise NotRevertibleError()
        if draft.id not in {d.id for d in await self._live()}:
            raise NotRevertibleError()  # pragma: no cover - só com dado anterior à 0053

        service = TenantService(self.session)
        row = await service.get_or_404(self.tenant.id)
        atual = await service.repo.settings(self.tenant.id)
        antes = dict(draft.previous_settings)

        if draft.branding:
            marca_antes = dict(antes.get("branding") or {})
            marca = dict(atual.get("branding") or {})
            for chave in AI_BRANDING_KEYS:
                if chave in marca_antes:
                    marca[chave] = marca_antes[chave]
                else:
                    marca.pop(chave, None)
            await service.set_setting(row, "branding", marca, self.actor)
        if draft.design:
            desenho = antes.get("design") or DesignV1().model_dump(mode="json")
            await service.set_setting(row, "design", dict(desenho), self.actor)
        pagina = dict(antes.get("landing") or {"blocks": []})
        pagina["blocks"] = _fresh_ids(list(pagina.get("blocks") or []))
        await service.set_setting(row, "landing", pagina, self.actor)

        agora = utcnow()
        draft.status = DraftStatus.REVERTED
        draft.replaced_at = agora
        draft.updated_by_actor = self.actor.id
        anterior_id = antes.get("live_draft_id")
        if isinstance(anterior_id, str):
            anterior = await self.session.get(LandingDraft, anterior_id)
            if anterior is not None and anterior.status == DraftStatus.SUPERSEDED:
                anterior.status = DraftStatus.APPLIED
                anterior.applied_at = agora
                anterior.replaced_at = None
                anterior.updated_by_actor = self.actor.id
        await self.session.flush()
        return draft

    async def edited_since(self, drafts: Sequence[LandingDraft]) -> set[str]:
        """Versões que a edição à mão deixou para trás.

        - proposta pronta montada antes da última edição: publicar substitui a edição;
        - a que está no ar, quando a loja foi editada depois de ela entrar: o que está no ar já
          não é exatamente ela.
        """
        alvo = [d for d in drafts if d.status in {DraftStatus.READY, DraftStatus.APPLIED}]
        if not alvo:
            return set()
        mudou = (
            (
                await self.session.execute(
                    select(TenantSetting.updated_at).where(TenantSetting.key.in_(PROPOSAL_KEYS))
                )
            )
            .scalars()
            .all()
        )
        ultima = max(mudou, default=None)
        if ultima is None:
            return set()
        folga = timedelta(seconds=EDIT_TOLERANCE_SECONDS)
        saida: set[str] = set()
        for d in alvo:
            if d.status == DraftStatus.READY:
                para_tras = d.created_at < ultima
            else:
                para_tras = d.applied_at is not None and d.applied_at + folga < ultima
            if para_tras:
                saida.add(d.id)
        return saida

    async def _live(self) -> builtins.list[LandingDraft]:
        """A versão no ar (no máximo uma; a lista cobre dado anterior à 0053)."""
        return list(
            (
                await self.session.execute(
                    select(LandingDraft)
                    .where(LandingDraft.status == str(DraftStatus.APPLIED))
                    .order_by(LandingDraft.applied_at.desc())
                )
            )
            .scalars()
            .all()
        )

    async def _without_stale_refs(
        self, blocks: builtins.list[dict[str, Any]]
    ) -> builtins.list[dict[str, Any]]:
        """Os blocos sem os ids que não existem mais nesta loja."""
        try:
            await check_setting_references(self.session, "landing", {"blocks": blocks})
        except ValidationError as exc:
            sumiram: set[str] = set()
            for campo in ("media_ids", "product_ids", "category_ids"):
                sumiram.update(str(x) for x in (exc.details.get(campo) or []))
            return drop_unknown_refs(blocks, sumiram)
        return blocks

    async def reset_brand_reading(self) -> None:
        """Esquece a leitura de cada arquivo: a próxima proposta olha todo o material de novo.

        O perfil fica — é o ponto de partida da releitura. Não gasta nada agora; a visão roda na
        próxima proposta, que custa a unidade de sempre.
        """
        linha = (await self.session.execute(select(BrandProfile))).scalar_one_or_none()
        if linha is None:
            return
        linha.materials = {}
        linha.brief_digest = None
        linha.updated_by_actor = self.actor.id
        await self.session.flush()

    async def discard(self, draft_id: str) -> LandingDraft:
        """Descarta uma proposta que ainda não foi publicada. Ela fica na lista e pode ser
        publicada depois, se a lojista mudar de ideia."""
        draft = await self.get_or_404(draft_id)
        if draft.status in {DraftStatus.QUEUED, DraftStatus.RUNNING}:
            # Parar no meio deixaria a cota sem quem a devolva e o worker escrevendo numa linha
            # que a lojista acha que descartou.
            raise DraftAlreadyRunningError("Espere a montagem terminar para descartar.")
        if draft.status != DraftStatus.READY:
            # A que está no ar sai por "desfazer"; as que já estiveram no ar são histórico.
            raise NotDiscardableError()
        draft.status = DraftStatus.DISCARDED
        draft.updated_by_actor = self.actor.id
        await self.session.flush()
        return draft

    # ------------------------------------------------------------------ apoio

    async def _brief(self) -> BriefV1:
        row = (await self.session.execute(select(LandingBrief))).scalar_one_or_none()
        return parse_brief(row.data) if row else BriefV1()


def _fresh_ids(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Os blocos sem id: a escrita (`normalize_landing`) dá ids novos.

    O normalizador recusa id que a página no ar não tem ("Bloco desconhecido") — é o que protege
    o editor de formulário velho. Uma página restaurada ou uma versão publicada de novo traz os
    ids de quando esteve no ar, que a página de agora não conhece: sem isto, desfazer uma
    publicação dava 422.
    """
    return [{**b, "id": None} if isinstance(b, dict) else b for b in blocks]
