from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import outbox
from app.core.logging import get_logger
from app.tenancy.dns import DnsVerifier
from app.tenancy.models import DomainStatus
from app.tenancy.repository import TenantRepository
from app.tenancy.service import TenantService
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_session

logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.deliver_event", bind=True, max_retries=0)
def deliver_event(self: object, event_id: str, consumer: str) -> str:
    del self

    async def _run(session: AsyncSession) -> str:
        return await outbox.deliver(session, event_id, consumer)

    return run_async(with_session(_run))


async def _dispatch(event_id: str, consumer: str) -> None:
    deliver_event.delay(event_id, consumer)


@celery_app.task(name="app.workers.tasks.relay_outbox")
def relay_outbox() -> int:
    async def _run(session: AsyncSession) -> int:
        return await outbox.relay_pending(session, _dispatch)

    count = run_async(with_session(_run))
    if count:
        logger.info("Outbox relayed", extra={"dispatched": count})
    return count


@celery_app.task(name="app.workers.tasks.retry_due_deliveries")
def retry_due_deliveries() -> int:
    async def _run(session: AsyncSession) -> int:
        due = await outbox.due_retries(session)
        for delivery in due:
            deliver_event.delay(delivery.event_id, delivery.consumer)
        return len(due)

    return run_async(with_session(_run))


@celery_app.task(name="app.workers.tasks.verify_domains")
def verify_domains() -> int:
    async def _run(session: AsyncSession) -> int:
        service = TenantService(session)
        verifier = DnsVerifier()
        domains = await service.repo.list_domains_to_verify()
        for domain in domains:
            try:
                await service.verify_domain(domain, verifier)
            except Exception:
                logger.exception("Domain verification failed", extra={"hostname": domain.hostname})
        return len(domains)

    return run_async(with_session(_run))


@celery_app.task(name="app.workers.tasks.recheck_active_domains")
def recheck_active_domains() -> int:
    async def _run(session: AsyncSession) -> int:
        service = TenantService(session)
        verifier = DnsVerifier()
        domains = [
            d
            for d in await TenantRepository(session).list_active_storefront_domains()
            if d.status == DomainStatus.ACTIVE
        ]
        for domain in domains:
            try:
                await service.recheck_active_domain(domain, verifier)
            except Exception:
                logger.exception("Domain recheck failed", extra={"hostname": domain.hostname})
        return len(domains)

    return run_async(with_session(_run))
