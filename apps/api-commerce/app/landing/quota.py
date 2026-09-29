"""Quantas propostas de vitrine a loja pode pedir, e quantas já pediu.

A torneira vem antes do que gasta. Reservar depois da chamada ao modelo é como se descobre, no
fim do mês, que o limite nunca segurou nada.

**A cota grátis funciona com o módulo desligado.** Se dependesse dele, "algumas propostas
incluídas" seria mentira: ninguém contrata para experimentar. A flag não é o portão de usar — é
o portão do teto maior. Mesmo caminho de código, um número diferente.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import DomainError
from app.landing.models import LandingGenerationUsage
from app.models.base import utcnow
from app.tenancy.context import TenantContext

#: A flag que abre o teto maior. Ligar é compra, e compra acontece na loja de serviços do site.
LANDING_AI_FLAG = "landing_ai"


class LandingQuotaExceededError(DomainError):
    status_code = 403
    error_code = "landing_quota_exceeded"
    message = "Você já usou as propostas deste mês."


@dataclass(frozen=True, slots=True)
class QuotaState:
    """O que a tela mostra **acima** do botão, nunca depois de clicar."""

    period: str
    used: int
    limit: int
    paid: bool

    @property
    def left(self) -> int:
        return max(0, self.limit - self.used)


def current_period() -> str:
    """ "AAAA-MM" em UTC. O mês é o do relógio da plataforma, não o do fuso da loja: a conta
    precisa fechar igual para todo mundo."""
    return utcnow().strftime("%Y-%m")


def limit_for(tenant: TenantContext) -> int:
    return (
        settings.landing_paid_generations_per_month
        if tenant.feature(LANDING_AI_FLAG)
        else settings.landing_free_generations_per_month
    )


class LandingQuotaService:
    def __init__(self, session: AsyncSession, tenant: TenantContext) -> None:
        self.session = session
        self.tenant = tenant

    async def state(self) -> QuotaState:
        """Quanto resta. Usado pela tela, antes de a pessoa pedir qualquer coisa."""
        period = current_period()
        row = await self._row(period, for_update=False)
        return QuotaState(
            period=period,
            used=row.used if row else 0,
            limit=limit_for(self.tenant),
            paid=self.tenant.feature(LANDING_AI_FLAG),
        )

    async def reserve(self) -> QuotaState:
        """Toma uma unidade agora, antes de qualquer gasto.

        A linha é travada para leitura e escrita (`FOR UPDATE`), então dois pedidos ao mesmo
        tempo não passam os dois. Sem isso o limite seria uma sugestão.
        """
        period = current_period()
        limit = limit_for(self.tenant)
        row = await self._row(period, for_update=True)
        if row is None:
            row = LandingGenerationUsage(tenant_id=self.tenant.id, period=period, used=0)
            self.session.add(row)
            try:
                await self.session.flush()
            except IntegrityError:
                # Outro pedido criou a linha no intervalo; pega a dele, agora travada.
                await self.session.rollback()
                row = await self._row(period, for_update=True)
                if row is None:  # pragma: no cover - só se a linha sumir entre as duas idas
                    raise
        if row.used >= limit:
            raise LandingQuotaExceededError(used=row.used, limit=limit, period=period)
        row.used += 1
        await self.session.flush()
        return QuotaState(
            period=period,
            used=row.used,
            limit=limit,
            paid=self.tenant.feature(LANDING_AI_FLAG),
        )

    async def release(self, period: str) -> None:
        """Devolve a unidade quando a proposta não chegou a existir.

        A loja pediu uma vez e não recebeu nada; cobrar por isso seria cobrar pelo nosso erro.
        O livro-caixa do outro lado continua com a chamada que custou — os dois números medem
        coisas diferentes, e é de propósito.
        """
        row = await self._row(period, for_update=True)
        if row is None or row.used <= 0:
            return
        row.used -= 1
        await self.session.flush()

    async def _row(self, period: str, *, for_update: bool) -> LandingGenerationUsage | None:
        stmt = select(LandingGenerationUsage).where(LandingGenerationUsage.period == period)
        if for_update:
            stmt = stmt.with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()
