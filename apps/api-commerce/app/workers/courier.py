"""Tarefas do entregador por aplicativo (Uber Direct, docs/15): o varredor das corridas."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.courier.service import run_couriers
from app.models.base import utcnow
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_factory

logger = get_logger(__name__)


@celery_app.task(name="app.workers.courier.run_couriers")
def run_couriers_task() -> int:
    """De 10 em 10 segundos: chama, tenta de novo, cancela e assume corrida de worker morto.

    É o varredor que chama a Uber depois do "Pronto" (o consumidor do outbox só reserva a linha,
    dentro da transação dele): a loja espera no máximo uma volta, e nenhuma transação fica aberta
    enquanto a Uber responde.
    """

    async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
        return await run_couriers(factory, utcnow())

    feitos = run_async(with_factory(_run))
    if feitos:
        logger.info("Corridas atualizadas", extra={"count": feitos})
    return feitos
