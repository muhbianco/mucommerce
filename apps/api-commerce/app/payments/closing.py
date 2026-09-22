"""Close the payment an order was waiting for, when the order stops waiting (cancelled by the
customer or the store, expired, or the customer gives up on it to pay another way).

Runs in the caller's transaction, under the order lock (order → payment, the global lock
order), with no network call: the payment is closed here and `next_check_at = now` hands the
provider-side cancel to reconciliation. If the customer paid in the meantime, reconciliation
sees the approval and the payment side flags it as a late payment.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.payments.events import emit_payment, record_event
from app.payments.models import Payment, PaymentStatus


async def close_active_payment(
    session: AsyncSession,
    tenant_id: str,
    actor_id: str,
    order_id: str,
    *,
    reason: str,
    payment_id: str | None = None,
) -> Payment | None:
    stmt = select(Payment).where(Payment.active_order_id == order_id)
    if payment_id is not None:
        stmt = stmt.where(Payment.id == payment_id)
    payment = await session.scalar(stmt.with_for_update().execution_options(populate_existing=True))
    if payment is None:
        return None
    now = utcnow()
    before = payment.status
    target = PaymentStatus.EXPIRED if reason == "expired" else PaymentStatus.CANCELLED
    payment.status = target
    payment.active_order_id = None
    payment.closed_at = now
    payment.next_check_at = now
    payment.check_attempts = 0
    payment.version += 1
    record_event(
        session, payment, "closed", before, target, actor_id=actor_id, detail={"reason": reason}
    )
    await session.flush()
    await emit_payment(session, tenant_id, payment, f"payment.{target}", reason=reason)
    return payment
