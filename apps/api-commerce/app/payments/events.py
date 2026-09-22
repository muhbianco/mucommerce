"""The payment event log and the payment outbox events, shared by the payment service and the
order side (which closes the open payment when an order is cancelled or expires)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.models.base import utcnow
from app.payments.models import Payment, PaymentEvent


def record_event(
    session: AsyncSession,
    payment: Payment,
    kind: str,
    before: str | None,
    after: str | None,
    *,
    actor_id: str,
    provider_status: str | None = None,
    http_status: int | None = None,
    duration_ms: int | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        PaymentEvent(
            payment_id=payment.id,
            kind=kind[:24],
            from_status=before,
            to_status=after,
            provider_status=(provider_status or "")[:40] or None,
            http_status=http_status,
            duration_ms=duration_ms,
            detail=detail,
            actor=actor_id,
            occurred_at=utcnow(),
        )
    )


async def emit_payment(
    session: AsyncSession, tenant_id: str, payment: Payment, event_type: str, **extra: Any
) -> None:
    await emit(
        session,
        aggregate_type="payment",
        aggregate_id=payment.id,
        event_type=event_type,
        payload={
            "payment_id": payment.id,
            "order_id": payment.order_id,
            "provider": payment.provider,
            "method": payment.method,
            "status": payment.status,
            "amount_cents": payment.amount_cents,
            **extra,
        },
        tenant_id=tenant_id,
    )
