"""Cancelling an order, money included: the one way an order is cancelled by a person.

Before payment, the held stock goes back and the open payment is closed (OrderService.cancel).
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
    await OrderService(session, tenant, actor).cancel(
        order, actor_kind, reason=reason, scopes=scopes, restock=restock
    )
    if not was_paid:
        return []
    kind = RefundKind.CUSTOMER_CANCEL if actor_kind == ActorKind.CUSTOMER else RefundKind.OPERATOR
    try:
        refund = await RefundService(session, tenant, actor).request(
            order, kind=kind, reason=reason or "Pedido cancelado"
        )
    except NothingToRefundError:
        return []  # nothing was charged (free order)
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
