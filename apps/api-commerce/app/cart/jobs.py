"""Cart housekeeping: converted carts are kept 90 days (support questions), then dropped."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cart.models import Cart, CartItem, CartStatus
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION

CART_RETENTION = timedelta(days=90)
CROSS = {CROSS_TENANT_OPTION: True}


async def purge_carts(session: AsyncSession, *, older_than: timedelta = CART_RETENTION) -> int:
    """Carts that became orders (or were abandoned) and saw no activity since the cutoff."""
    cutoff = utcnow() - older_than
    old = (
        select(Cart.id)
        .where(Cart.status != CartStatus.ACTIVE)
        .where(Cart.last_activity_at < cutoff)
        .limit(5000)
        .execution_options(**CROSS)
    )
    ids = list((await session.execute(old)).scalars())
    if not ids:
        return 0
    await session.execute(
        delete(CartItem).where(CartItem.cart_id.in_(ids)).execution_options(**CROSS)
    )
    await session.execute(delete(Cart).where(Cart.id.in_(ids)).execution_options(**CROSS))
    return len(ids)
