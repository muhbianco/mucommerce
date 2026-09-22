"""Payment jobs run by the beat (and by the E2E server's /__e2e/tick).

`run_reconcile_payments`: every payment with a due `next_check_at` — open ones are asked about
(a lost webhook costs minutes, not the sale), ones we closed are cancelled at the provider. Each
payment is handled in its own session; PaymentService does the provider round trip with nothing
locked and schedules the next check.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.payments.models import ACTIVE_PAYMENT_STATUSES, Payment, PaymentStatus
from app.payments.service import PaymentService
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

logger = get_logger(__name__)
BATCH = 100
ACTOR = Actor.system("payment-reconciliation")
# What reconcile acts on; leading the index (status, next_check_at) keeps the sweep off a scan.
CHECKED = frozenset(ACTIVE_PAYMENT_STATUSES | {PaymentStatus.CANCELLED, PaymentStatus.EXPIRED})


async def due_checks(session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    stmt = (
        select(Payment.id, Payment.tenant_id)
        .where(Payment.status.in_(CHECKED))
        .where(Payment.next_check_at <= now)
        .order_by(Payment.next_check_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return [(pid, tid) for pid, tid in (await session.execute(stmt)).all()]


async def reconcile_one(
    factory: async_sessionmaker[AsyncSession], payment_id: str, tenant_id: str, now: datetime
) -> str:
    async with factory() as session:
        tenant = await TenantResolver(session).resolve_by_id(tenant_id)
        bind_session_tenant(session, tenant_id)
        outcome = await PaymentService(session, tenant, ACTOR).reconcile(payment_id, now)
        await session.commit()
        return outcome


async def run_reconcile_payments(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with factory() as session:
        due = await due_checks(session, now)
    changed = 0
    for payment_id, tenant_id in due:
        try:
            changed += await reconcile_one(factory, payment_id, tenant_id, now) == "changed"
        except Exception:
            logger.exception("Payment reconciliation failed", extra={"payment_id": payment_id})
    return changed
