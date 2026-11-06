"""Pedir, olhar, publicar, restaurar e descartar propostas de desenho da loja.

A regra que organiza o arquivo inteiro: **publicar uma proposta é a mesma escrita que a edição à
mão.** `TenantService.set_setting` valida, confere referências e registra auditoria; um atalho
daqui direto para `tenant_settings` significaria dois caminhos de escrita e, mais cedo do que se
imagina, um deles esquecido numa mudança. Há uma regra em `test_architecture.py` cobrando isso.

**Publicar não é sem volta.** Antes de escrever, a proposta guarda o que estava no ar
(`previous_settings`); restaurar escreve isso de volta, pela mesma porta. A SGPipas publicou
uma proposta 48 minutos depois de editar a página à mão e perdeu a edição sem aviso e sem
caminho de volta (docs/16 §A.1).

A ordem do pedido também é deliberada: **reserva a cota, depois enfileira.** Contar depois é como
se descobre, no fim do mês, que o limite nunca segurou nada.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.core.config import settings
from app.core.exceptions import ConflictError, DomainError, NotFoundError, ValidationError
from app.landing.brief import parse_brief
from app.landing.design import AI_BRANDING_KEYS, DesignV1
from app.landing.design_rules import effective_branding
from app.landing.models import BrandProfile, DraftSource, DraftStatus, LandingBrief, LandingDraft
from app.landing.quota import LandingQuotaService
from app.landing.schemas import BriefV1
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.models import TenantSetting
from app.tenancy.service import Actor, TenantService

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
    message = "Só dá para restaurar a última proposta publicada."


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
            .limit(settings.landing_drafts_kept + MAX_IN_FLIGHT)
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
        """Publica a proposta: marca, desenho por área e página inicial, nesta ordem.

        **Pela mesma porta da edição à mão**: `set_setting` valida, confere as referências
        contra as linhas desta loja e escreve a auditoria — uma linha por chave. O modelo nunca
        toca em `tenant_settings`. A marca é mesclada sobre a de agora, e só nas chaves que a IA
        pode mexer: um logotipo trocado depois da proposta continua o novo.

        Devolve o valor gravado da página inicial (o contrato antigo da rota).
        """
        draft = await self.get_or_404(draft_id)
        if draft.status != DraftStatus.READY or not draft.blocks:
            raise DraftNotReadyError()

        service = TenantService(self.session)
        row = await service.get_or_404(self.tenant.id)
        atual = await service.repo.settings(self.tenant.id)
        anterior = {key: atual.get(key) for key in PROPOSAL_KEYS}

        if draft.branding:
            marca = effective_branding(dict(atual.get("branding") or {}), draft.branding)
            await service.set_setting(row, "branding", marca, self.actor)
        if draft.design:
            await service.set_setting(row, "design", dict(draft.design), self.actor)
        saved = await service.set_setting(row, "landing", {"blocks": draft.blocks}, self.actor)

        draft.previous_settings = anterior
        draft.status = DraftStatus.APPLIED
        draft.applied_at = utcnow()
        draft.updated_by_actor = self.actor.id
        await self.session.flush()
        return saved

    async def revert(self, draft_id: str) -> LandingDraft:
        """Devolve ao ar o que estava antes desta proposta.

        Só a última publicada: restaurar uma mais antiga apagaria o que veio depois dela sem a
        lojista ver. A marca volta só nas chaves da IA (o logotipo atual fica); desenho e página
        voltam inteiros. Chave que não existia antes volta ao padrão.
        """
        draft = await self.get_or_404(draft_id)
        if draft.status != DraftStatus.APPLIED or draft.previous_settings is None:
            raise NotRevertibleError()
        ultima = (
            await self.session.execute(
                select(LandingDraft.id)
                .where(LandingDraft.status == str(DraftStatus.APPLIED))
                .order_by(LandingDraft.applied_at.desc(), LandingDraft.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if ultima != draft.id:
            raise NotRevertibleError()

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
        await service.set_setting(
            row, "landing", dict(antes.get("landing") or {"blocks": []}), self.actor
        )

        draft.status = DraftStatus.REVERTED
        draft.updated_by_actor = self.actor.id
        await self.session.flush()
        return draft

    async def edited_since(self, drafts: Sequence[LandingDraft]) -> set[str]:
        """Propostas prontas que ficaram para trás: o que ela publicaria foi editado à mão
        depois de a proposta ser montada. A tela avisa antes de publicar por cima."""
        prontas = [d for d in drafts if d.status == DraftStatus.READY]
        if not prontas:
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
        return {d.id for d in prontas if d.created_at < ultima}

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
        draft = await self.get_or_404(draft_id)
        if draft.status in {DraftStatus.QUEUED, DraftStatus.RUNNING}:
            # Parar no meio deixaria a cota sem quem a devolva e o worker escrevendo numa linha
            # que a lojista acha que descartou.
            raise DraftAlreadyRunningError("Espere a montagem terminar para descartar.")
        draft.status = DraftStatus.DISCARDED
        draft.updated_by_actor = self.actor.id
        await self.session.flush()
        return draft

    # ------------------------------------------------------------------ apoio

    async def _brief(self) -> BriefV1:
        row = (await self.session.execute(select(LandingBrief))).scalar_one_or_none()
        return parse_brief(row.data) if row else BriefV1()
