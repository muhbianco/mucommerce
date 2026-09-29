"""A faxina da montagem da vitrine. Roda entre as lojas, pelo job de manutenção.

Uma sujeira, e ela custa dinheiro e confiança se ninguém varrer: **rascunho preso em
`running`**. O worker morreu no meio (reinício, OOM, deploy). Sem isto a tela fica "montando…"
para sempre e a lojista perde uma proposta que nunca viu. Vira `failed` com motivo legível **e a
cota volta** — é a única direção segura: já pagamos a chamada, ela não precisa pagar também.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.landing.models import DraftStatus, LandingDraft
from app.landing.quota import LandingQuotaService
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.resolver import TenantResolver

logger = get_logger(__name__)

#: Uma geração tem timeout de 75 s e no máximo duas tentativas. Dez minutos é folga larga o
#: bastante para nunca matar trabalho vivo, e curta o bastante para a lojista não desistir.
STUCK_AFTER = timedelta(minutes=10)


async def sweep_stuck_drafts(session: AsyncSession) -> int:
    """Rascunho parado em `running` há tempo demais: falha e devolve a cota."""
    limite = utcnow() - STUCK_AFTER
    rows = (
        (
            await session.execute(
                select(LandingDraft)
                .where(LandingDraft.status == DraftStatus.RUNNING)
                .where(LandingDraft.updated_at < limite)
                .order_by(LandingDraft.updated_at)
                .limit(100)
                .execution_options(**{CROSS_TENANT_OPTION: True})
            )
        )
        .scalars()
        .all()
    )
    for draft in rows:
        bind_session_tenant(session, draft.tenant_id)
        draft.status = DraftStatus.FAILED
        draft.failure_reason = (
            "A montagem não terminou. Tente de novo — a proposta não foi cobrada."
        )
        context = await TenantResolver(session).resolve_by_id(draft.tenant_id)
        await LandingQuotaService(session, context).release(draft.quota_period)
    await session.flush()
    return len(rows)
