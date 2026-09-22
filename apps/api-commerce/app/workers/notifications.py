"""E-mail tasks (queue commerce.notifications): sending what the notifier queued."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utcnow
from app.notifications.jobs import run_send_notifications
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_factory

logger = get_logger(__name__)


@celery_app.task(name="app.workers.notifications.send_notifications")
def send_notifications() -> int:
    """Every 15 s: e-mails waiting (new ones, retries, leases that ran out)."""

    async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
        return await run_send_notifications(factory, utcnow())

    sent = run_async(with_factory(_run))
    if sent:
        logger.info("E-mails sent", extra={"count": sent})
    return sent
