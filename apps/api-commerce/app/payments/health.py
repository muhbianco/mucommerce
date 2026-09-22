"""What "money is stuck" looks like, counted across every store (ADR 0011, stage E S15).

These are the shapes that mean a customer paid and the store does not know, or the store owes
money and nobody noticed. The beat counts them every five minutes and logs each one it finds as
`payment_alert` at error level — Sentry's logging integration turns that into an alert — and
`GET /ops/payments/health` shows the same numbers on demand.

Every query is a bounded count over an index; none of them touches a store's secrets.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inventory.service import reserved_mismatches
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.orders.models import Order
from app.orders.state_machine import OrderStatus
from app.payments.models import (
    ACTIVE_PAYMENT_STATUSES,
    InboxStatus,
    Payment,
    PaymentStatus,
    PaymentWebhookInbox,
    Refund,
    RefundStatus,
)
from app.tenancy.context import CROSS_TENANT_OPTION

CROSS = {CROSS_TENANT_OPTION: True}
# How long each shape may last before it counts as a problem.
PAID_NOT_HANDLED = timedelta(minutes=5)
PAYMENT_OVERDUE = timedelta(minutes=10)
ORDER_OVERDUE = timedelta(minutes=5)
REFUND_STUCK = timedelta(hours=1)
WEBHOOK_WINDOW = timedelta(minutes=15)
EMAIL_WINDOW = timedelta(hours=24)


async def _count(session: AsyncSession, stmt: object) -> int:
    value = await session.scalar(
        select(func.count()).select_from(stmt.subquery()).execution_options(**CROSS)  # type: ignore[attr-defined]
    )
    return int(value or 0)


async def payment_anomalies(session: AsyncSession, now: datetime) -> dict[str, int]:
    """Named counts, all of them zero when nothing is stuck."""
    approved_unhandled = (
        select(Payment.id)
        .join(Order, (Order.tenant_id == Payment.tenant_id) & (Order.id == Payment.order_id))
        .where(Payment.status == PaymentStatus.APPROVED)
        .where(Payment.approved_at < now - PAID_NOT_HANDLED)
        .where(Order.paid_at.is_(None))
        .where(Order.status != OrderStatus.CANCELLED)
        .limit(500)
    )
    payments_overdue = (
        select(Payment.id)
        .where(Payment.status.in_(ACTIVE_PAYMENT_STATUSES))
        .where(Payment.expires_at < now - PAYMENT_OVERDUE)
        .limit(500)
    )
    orders_overdue = (
        select(Order.id)
        .where(Order.status == OrderStatus.AWAITING_PAYMENT)
        .where(Order.expires_at < now - ORDER_OVERDUE)
        .limit(500)
    )
    webhooks_refused = (
        select(PaymentWebhookInbox.id)
        .where(PaymentWebhookInbox.status.in_((InboxStatus.INVALID, InboxStatus.FAILED)))
        .where(PaymentWebhookInbox.received_at > now - WEBHOOK_WINDOW)
        .limit(500)
    )
    refunds_stuck = (
        select(Refund.id)
        .where(Refund.status == RefundStatus.PROCESSING)
        .where(Refund.updated_at < now - REFUND_STUCK)
        .limit(500)
    )
    refunds_failed = select(Refund.id).where(Refund.status == RefundStatus.FAILED).limit(500)
    emails_failed = (
        select(NotificationDelivery.id)
        .where(NotificationDelivery.status == DeliveryStatus.FAILED)
        .where(NotificationDelivery.updated_at > now - EMAIL_WINDOW)
        .limit(500)
    )
    return {
        "paid_not_handled": await _count(session, approved_unhandled),
        "payments_overdue": await _count(session, payments_overdue),
        "orders_overdue": await _count(session, orders_overdue),
        "webhooks_refused": await _count(session, webhooks_refused),
        "refunds_stuck": await _count(session, refunds_stuck),
        "refunds_failed": await _count(session, refunds_failed),
        "emails_failed": await _count(session, emails_failed),
        "reserved_mismatch": await reserved_mismatches(session),
    }
