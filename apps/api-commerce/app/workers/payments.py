"""Payment tasks (queue commerce.payments): webhook processing and reconciliation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utcnow
from app.payments import webhooks
from app.payments.jobs import run_reconcile_payments
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_factory

logger = get_logger(__name__)


@celery_app.task(name="app.workers.payments.process_webhook")
def process_webhook(inbox_id: str) -> str:
    """Right after a webhook is stored. A lost message is picked up by the sweep."""

    async def _run(factory: async_sessionmaker[AsyncSession]) -> str:
        async with factory() as session:
            return await webhooks.process(session, inbox_id)

    return run_async(with_factory(_run))


@celery_app.task(name="app.workers.payments.sweep_webhooks")
def sweep_webhooks() -> int:
    """Every 30 s: webhooks still waiting (lost task, provider down, retry due)."""

    async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
        return await webhooks.run_process_webhooks(factory, utcnow())

    processed = run_async(with_factory(_run))
    if processed:
        logger.info("Payment webhooks swept", extra={"count": processed})
    return processed


@celery_app.task(name="app.workers.payments.reconcile_payments")
def reconcile_payments() -> int:
    """Every minute: due payment checks (open payments asked about, closed ones cancelled at
    the provider)."""

    async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
        return await run_reconcile_payments(factory, utcnow())

    changed = run_async(with_factory(_run))
    if changed:
        logger.info("Payments reconciled", extra={"changed": changed})
    return changed
