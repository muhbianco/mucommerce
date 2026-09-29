"""Pedir, olhar, aplicar e descartar propostas de página inicial.

A regra que organiza o arquivo inteiro: **aplicar uma proposta é a mesma escrita que a edição à
mão.** `TenantService.set_setting` valida, confere referências e registra auditoria; um atalho
daqui direto para `tenant_settings` significaria dois caminhos de escrita e, mais cedo do que se
imagina, um deles esquecido numa mudança. Há uma regra em `test_architecture.py` cobrando isso.

A ordem do pedido também é deliberada: **reserva a cota, depois enfileira.** Contar depois é como
se descobre, no fim do mês, que o limite nunca segurou nada.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.core.config import settings
from app.core.exceptions import ConflictError, DomainError, NotFoundError, ValidationError
from app.landing.brief import parse_brief
from app.landing.models import DraftSource, DraftStatus, LandingBrief, LandingDraft
from app.landing.quota import LandingQuotaService
from app.landing.schemas import BriefV1
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor, TenantService

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
        """Publica a proposta.

        **Pela mesma porta da edição à mão**: `set_setting` valida, confere as referências contra
        as linhas desta loja e escreve a auditoria. O modelo nunca toca em `tenant_settings`.
        """
        draft = await self.get_or_404(draft_id)
        if draft.status != DraftStatus.READY or not draft.blocks:
            raise DraftNotReadyError()

        service = TenantService(self.session)
        row = await service.get_or_404(self.tenant.id)
        saved = await service.set_setting(row, "landing", {"blocks": draft.blocks}, self.actor)

        draft.status = DraftStatus.APPLIED
        draft.applied_at = utcnow()
        draft.updated_by_actor = self.actor.id
        await self.session.flush()
        return saved

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
