"""A faxina da montagem da vitrine. Roda entre as lojas, pelo job de manutenção.

Duas sujeiras, e as duas custam dinheiro ou confiança se ninguém varrer:

- **rascunho preso em `running`.** O worker morreu no meio (reinício, OOM, deploy). Sem isto a
  tela fica "montando…" para sempre e a lojista perde uma proposta que nunca viu. Vira `failed`
  com motivo legível **e a cota volta** — é a única direção segura: já pagamos a chamada, ela não
  precisa pagar por ela também;
- **material de brief que ninguém referencia.** São fotos que a lojista mandou só para a gente
  entender o negócio dela; guardá-las para sempre é acumular imagem de gente que talvez nem seja
  cliente mais.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.core.config import settings
from app.core.logging import get_logger
from app.landing.models import DraftStatus, LandingDraft
from app.landing.quota import LandingQuotaService
from app.media.models import MediaAsset, MediaOwner
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.resolver import TenantResolver

logger = get_logger(__name__)

#: Uma geração tem timeout de 75 s e no máximo duas tentativas. Dez minutos é folga larga o
#: bastante para nunca matar trabalho vivo, e curta o bastante para a lojista não desistir.
STUCK_AFTER = timedelta(minutes=10)

#: Material de brief sem rascunho que o referencie. Trinta dias porque a lojista pode responder o
#: questionário num sábado e só voltar a mexer no mês seguinte.
BRIEF_MEDIA_KEPT = timedelta(days=30)


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


async def sweep_brief_media(session: AsyncSession) -> int:
    """Imagem de brief antiga que nenhum rascunho usa.

    A conferência é contra os `inventory_snapshot` guardados: se uma proposta ainda explica a si
    mesma com aquela imagem, ela fica. As demais são insumo vencido.
    """
    limite = utcnow() - BRIEF_MEDIA_KEPT
    rows = (
        (
            await session.execute(
                select(MediaAsset)
                .where(MediaAsset.owner_type == MediaOwner.BRIEF)
                .where(MediaAsset.updated_at < limite)
                .order_by(MediaAsset.updated_at)
                .limit(200)
                .execution_options(**{CROSS_TENANT_OPTION: True})
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0

    tenants = {row.tenant_id for row in rows}
    em_uso: set[str] = set()
    for tenant_id in tenants:
        bind_session_tenant(session, tenant_id)
        drafts = (
            (
                await session.execute(
                    select(LandingDraft.inventory_snapshot).where(
                        LandingDraft.inventory_snapshot.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        for snapshot in drafts:
            for item in (snapshot or {}).get("media") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    em_uso.add(item["id"])

    apagados = 0
    for row in rows:
        if row.id in em_uso:
            continue
        bind_session_tenant(session, row.tenant_id)
        # O evento é o que apaga o arquivo no storage. Sumir só com a linha deixaria o objeto
        # órfão para sempre: ninguém mais sabe que ele existe, e ele continua ocupando o balde.
        await emit(
            session,
            aggregate_type="media",
            aggregate_id=row.id,
            event_type="media.deleted",
            payload={
                "media_id": row.id,
                "objects": {
                    settings.storage_private_bucket: [row.upload_key],
                    settings.storage_public_bucket: [
                        str(info["key"]) for info in (row.renditions or {}).values()
                    ],
                },
            },
            tenant_id=row.tenant_id,
        )
        await session.delete(row)
        apagados += 1
    await session.flush()
    return apagados
