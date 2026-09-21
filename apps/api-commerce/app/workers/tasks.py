from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import idempotency, outbox
from app.core.logging import get_logger
from app.customers.repository import purge_auth_flows, purge_sessions
from app.identity.repository import AdminUserRepository
from app.inventory.service import audit_ledger
from app.tenancy.dns import DnsVerifier
from app.tenancy.models import DomainStatus
from app.tenancy.repository import TenantRepository
from app.tenancy.service import TenantService
from app.workers import consumers as _consumers  # noqa: F401  (registers outbox consumers)
from app.workers.celery_app import celery_app
from app.workers.runtime import run_async, with_session

logger = get_logger(__name__)


@celery_app.task(name="app.workers.tasks.deliver_event", bind=True, max_retries=0)
def deliver_event(self: object, event_id: str, consumer: str) -> str:
    del self

    async def _run(session: AsyncSession) -> str:
        return await outbox.deliver(session, event_id, consumer)

    return run_async(with_session(_run))


def _dispatch(to_dispatch: outbox.DispatchList) -> None:
    """Enqueue deliveries. Only call after the transaction that created them committed."""
    for event_id, consumer in to_dispatch:
        deliver_event.delay(event_id, consumer)


@celery_app.task(name="app.workers.tasks.relay_outbox")
def relay_outbox() -> int:
    async def _run(session: AsyncSession) -> outbox.DispatchList:
        return await outbox.relay_pending(session)

    to_dispatch = run_async(with_session(_run))  # committed on return
    _dispatch(to_dispatch)
    if to_dispatch:
        logger.info("Outbox relayed", extra={"dispatched": len(to_dispatch)})
    return len(to_dispatch)


@celery_app.task(name="app.workers.tasks.retry_due_deliveries")
def retry_due_deliveries() -> int:
    async def _run(session: AsyncSession) -> outbox.DispatchList:
        return await outbox.claim_due_deliveries(session)

    to_dispatch = run_async(with_session(_run))  # committed on return
    _dispatch(to_dispatch)
    if to_dispatch:
        logger.info("Outbox retries dispatched", extra={"dispatched": len(to_dispatch)})
    return len(to_dispatch)


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


REFRESH_TOKEN_RETENTION = timedelta(days=7)
CUSTOMER_FLOW_RETENTION = timedelta(days=1)
CUSTOMER_SESSION_RETENTION = timedelta(days=7)


@celery_app.task(name="app.workers.tasks.purge_expired_records")
def purge_expired_records() -> dict[str, int]:
    """Daily: idempotency keys past their TTL, refresh tokens expired for a week, customer
    sign-in flows older than a day, customer sessions expired or revoked for a week."""

    async def _run(session: AsyncSession) -> dict[str, int]:
        return {
            "idempotency_keys": await idempotency.purge_expired(session),
            "refresh_tokens": await AdminUserRepository(session).purge_expired_refresh(
                older_than=REFRESH_TOKEN_RETENTION
            ),
            "customer_auth_flows": await purge_auth_flows(
                session, older_than=CUSTOMER_FLOW_RETENTION
            ),
            "customer_sessions": await purge_sessions(
                session, older_than=CUSTOMER_SESSION_RETENTION
            ),
        }

    purged = run_async(with_session(_run))
    logger.info("Expired records purged", extra=purged)
    return purged


@celery_app.task(name="app.workers.tasks.audit_inventory_ledger")
def audit_inventory_ledger() -> int:
    """Daily: every balance equals the sum of its movements. Mismatches are logged as errors."""
    mismatches = run_async(with_session(audit_ledger))
    if mismatches:
        logger.error("Inventory ledger audit found mismatches", extra={"count": mismatches})
    return mismatches
