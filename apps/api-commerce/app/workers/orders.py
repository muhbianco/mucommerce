"""Order tasks (queue commerce.payments): the payment deadline."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utcnow
from app.orders.jobs import run_expire_orders
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_factory

logger = get_logger(__name__)


@celery_app.task(name="app.workers.orders.expire_orders")
def expire_orders() -> int:
    """Every minute: orders awaiting payment past their deadline fail and free their stock."""

    async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
        return await run_expire_orders(factory, utcnow())

    expired = run_async(with_factory(_run))
    if expired:
        logger.info("Orders expired", extra={"count": expired})
    return expired
