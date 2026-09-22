"""Order jobs run by the beat (and by the E2E server's /__e2e/tick).

`run_expire_orders`: orders awaiting payment past their deadline fail and free their stock.
Candidates are listed across stores without locks; each one is then handled in its own
transaction, locking the order with SKIP LOCKED (a customer paying or cancelling it right now
keeps it) and checking status and deadline again under the lock, so a second run — or two
beats at once — changes nothing.

An order with an open payment is not given up on blindly: the provider is asked first (an
approval wins over the deadline), and the order waits up to GRACE past its deadline for the
provider's last word — never beyond the store-wide limit on an order's age.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.logging import get_logger
from app.orders.models import Order
from app.orders.service import OrderService
from app.orders.state_machine import OrderStatus
from app.payments.models import Payment
from app.payments.service import GRACE, PaymentService
from app.tenancy.context import CROSS_TENANT_OPTION, TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

logger = get_logger(__name__)
BATCH = 100
ACTOR = Actor.system("expire-orders")


async def due_orders(session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    stmt = (
        select(Order.id, Order.tenant_id)
        .where(Order.status == OrderStatus.AWAITING_PAYMENT)
        .where(Order.expires_at <= now)
        .order_by(Order.expires_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return [(order_id, tenant_id) for order_id, tenant_id in (await session.execute(stmt)).all()]


async def expire_one(
    factory: async_sessionmaker[AsyncSession], order_id: str, tenant_id: str, now: datetime
) -> bool:
    async with factory() as session:
        tenant = await TenantResolver(session).resolve_by_id(tenant_id)
        bind_session_tenant(session, tenant_id)
        if not await _payment_settled(session, tenant, order_id, now):
            return False
        order = await session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if order is None:
            return False  # someone else holds it (paying or cancelling): next run
        expired = await OrderService(session, tenant, ACTOR).expire(order, now)
        await session.commit()
        return expired


async def _payment_settled(
    session: AsyncSession, tenant: TenantContext, order_id: str, now: datetime
) -> bool:
    """True when the order may expire now as far as payments go: no open payment, or the
    provider still says unpaid and the grace is over. Commits (the provider is asked with no
    transaction open)."""
    open_payment = await session.scalar(
        select(Payment.id).where(Payment.active_order_id == order_id)
    )
    if open_payment is None:
        return True
    await PaymentService(session, tenant, ACTOR).sync(open_payment, source="expiry")
    await session.commit()
    order = await session.get(Order, order_id, populate_existing=True)
    if order is None or order.status != OrderStatus.AWAITING_PAYMENT or order.expires_at is None:
        return False  # paid (or otherwise settled) just now
    still_open = await session.scalar(select(Payment.id).where(Payment.active_order_id == order_id))
    hard_limit = order.placed_at + timedelta(minutes=settings.checkout_max_order_age_minutes)
    return still_open is None or now >= min(order.expires_at + GRACE, hard_limit)


async def run_expire_orders(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with factory() as session:
        due = await due_orders(session, now)
    done = 0
    for order_id, tenant_id in due:
        try:
            done += await expire_one(factory, order_id, tenant_id, now)
        except Exception:
            logger.exception("Order expiry failed", extra={"order_id": order_id})
    return done
