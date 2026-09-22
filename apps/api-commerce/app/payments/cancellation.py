"""Cancelling an order, money included: the one way an order is cancelled by a person.

Before payment, the open payment is closed and the held stock goes back (OrderService.cancel).
After payment, the stock goes back too (when asked) and a refund of what was paid is requested
in the same transaction — by policy for the customer (within the store's window), under the
four-eyes rule for the store's team. The caller holds the order lock, commits, then hands the
approved refunds to `dispatch_refunds`.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.orders.models import Order
from app.orders.service import OrderService
from app.orders.state_machine import PAID_STATUSES, ActorKind
from app.payments.models import Refund, RefundKind, RefundMethod, RefundStatus
from app.payments.refunds import NothingToRefundError, RefundService
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor
from app.workers.payments import process_refund

logger = get_logger(__name__)


async def cancel_order(
    session: AsyncSession,
    tenant: TenantContext,
    actor: Actor,
    order: Order,
    actor_kind: ActorKind,
    *,
    reason: str,
    scopes: frozenset[str] = frozenset(),
    restock: bool = True,
) -> list[Refund]:
    was_paid = order.status in PAID_STATUSES
    refunds = RefundService(session, tenant, actor)
    # The payment is locked before the stock goes back (order → payment → coupon → balances).
    payment = await refunds.refundable_payment(order, required=False) if was_paid else None
    await OrderService(session, tenant, actor).cancel(
        order, actor_kind, reason=reason, scopes=scopes, restock=restock
    )
    if payment is None:
        return []  # nothing was charged (free order), or it was never paid
    kind = RefundKind.CUSTOMER_CANCEL if actor_kind == ActorKind.CUSTOMER else RefundKind.OPERATOR
    try:
        refund = await refunds.request(
            order, kind=kind, reason=reason or "Pedido cancelado", payment_id=payment.id
        )
    except NothingToRefundError:
        return []
    return [refund]


async def dispatch_refunds(
    session: AsyncSession, tenant: TenantContext, refunds: list[Refund]
) -> None:
    """After the commit: send approved provider refunds now (a task, or inline without a broker).
    A failure here is not the caller's: the refund sweep sends anything left within a minute."""
    for refund in refunds:
        if refund.status != RefundStatus.APPROVED or refund.method != RefundMethod.PROVIDER:
            continue
        try:
            if settings.celery_broker_url:
                await asyncio.to_thread(process_refund.delay, refund.id)
            else:
                await RefundService(session, tenant, Actor.system("refunds")).process(refund.id)
                await session.commit()
        except Exception:
            logger.exception("Refund dispatch failed", extra={"refund_id": refund.id})
