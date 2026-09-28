"""Tarefas de envio (fila commerce.payments): rastreio das remessas abertas."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utcnow
from app.shipping.jobs import run_track_shipments
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_factory

logger = get_logger(__name__)


@celery_app.task(name="app.workers.shipping.track_shipments")
def track_shipments() -> int:
    """De 10 em 10 minutos: pergunta o rastreio das remessas abertas.

    O lote é pequeno e cada remessa só é consultada a cada 4h — quem limita a frequência é a
    própria remessa (`tracked_at`), não este intervalo.
    """

    async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
        return await run_track_shipments(factory, utcnow())

    mudaram = run_async(with_factory(_run))
    if mudaram:
        logger.info("Remessas atualizadas", extra={"changed": mudaram})
    return mudaram
