"""Coupons: what the store sets up, and what happens when one is used.

Reading (the cart's preview) never locks. Using one does: `place` takes the coupon's row lock
after the cart and before the balances (the global lock order), counts again under that lock and
only then raises `redemptions_count` and writes the redemption — so a coupon limited to N is
used N times, no matter how many people check out at the same second.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import audit
from app.core.exceptions import ConflictError, NotFoundError
from app.coupons.models import Coupon, CouponRedemption, CouponStatus, RedemptionStatus
from app.coupons.rules import CouponOutcome, evaluate
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

MAX_CODE = 40


class CouponInvalidError(NotFoundError):
    """The code does not exist for this store, or cannot be used now."""

    error_code = "coupon_invalid"
    message = "Este cupom não pode ser usado neste pedido."


class CouponCodeTakenError(ConflictError):
    error_code = "coupon_code_taken"
    message = "Já existe um cupom com esse código nesta loja."


def normalize(code: str) -> str:
    return code.strip().upper()[:MAX_CODE]


class CouponService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor

    # ------------------------------------------------------------------ reading
    async def by_code(self, code: str, *, lock: bool = False) -> Coupon | None:
        stmt = select(Coupon).where(Coupon.code == normalize(code))
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        coupon: Coupon | None = await self.session.scalar(stmt)
        return coupon

    async def customer_uses(self, coupon_id: str, customer_id: str) -> int:
        used = await self.session.scalar(
            select(func.count())
            .select_from(CouponRedemption)
            .where(CouponRedemption.coupon_id == coupon_id)
            .where(CouponRedemption.customer_id == customer_id)
            .where(CouponRedemption.status == RedemptionStatus.ACTIVE)
        )
        return int(used or 0)

    async def check(
        self, code: str, *, subtotal_cents: int, customer_id: str, now: datetime, lock: bool = False
    ) -> tuple[Coupon | None, CouponOutcome]:
        """The coupon and what it is worth right now (`lock=True` under the place lock)."""
        coupon = await self.by_code(code, lock=lock)
        uses = await self.customer_uses(coupon.id, customer_id) if coupon else 0
        return coupon, evaluate(coupon, subtotal_cents=subtotal_cents, customer_uses=uses, now=now)

    async def listing(self, *, limit: int, before_id: str | None = None) -> list[Coupon]:
        stmt = select(Coupon).order_by(Coupon.id.desc()).limit(limit + 1)
        if before_id is not None:
            stmt = stmt.where(Coupon.id < before_id)
        return list((await self.session.execute(stmt)).scalars())

    async def get(self, coupon_id: str) -> Coupon:
        coupon = await self.session.get(Coupon, coupon_id)
        if coupon is None:
            raise NotFoundError("Cupom não encontrado.")
        return coupon

    # ------------------------------------------------------------------ writing
    async def create(self, code: str, changes: Mapping[str, Any]) -> Coupon:
        normalized = normalize(code)
        if await self.by_code(normalized) is not None:
            raise CouponCodeTakenError(code=normalized)
        coupon = Coupon(code=normalized, redemptions_count=0)
        _apply(coupon, changes)
        coupon.created_by_actor = self.actor.id
        coupon.updated_by_actor = self.actor.id
        self.session.add(coupon)
        await self.session.flush()
        await self._audit("coupon.created", coupon)
        return coupon

    async def update(self, coupon_id: str, changes: Mapping[str, Any]) -> Coupon:
        """Only the fields the caller sent change (the code itself never does: it is printed)."""
        coupon = await self.get(coupon_id)
        _apply(coupon, changes)
        coupon.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit("coupon.updated", coupon)
        return coupon

    async def redeem(
        self, coupon: Coupon, *, order_id: str, customer_id: str, discount_cents: int
    ) -> CouponRedemption:
        """Under the coupon's lock, inside the transaction that creates the order."""
        coupon.redemptions_count += 1
        redemption = CouponRedemption(
            coupon_id=coupon.id,
            order_id=order_id,
            customer_id=customer_id,
            discount_cents=discount_cents,
        )
        self.session.add(redemption)
        await self.session.flush()
        return redemption

    async def release(self, order_id: str) -> bool:
        """The order expired or was cancelled before payment: the place is free again."""
        redemption = await self.session.scalar(
            select(CouponRedemption)
            .where(CouponRedemption.order_id == order_id)
            .where(CouponRedemption.status == RedemptionStatus.ACTIVE)
        )
        if redemption is None:
            return False
        coupon = await self.session.scalar(
            select(Coupon)
            .where(Coupon.id == redemption.coupon_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        redemption.status = RedemptionStatus.RELEASED
        redemption.released_at = utcnow()
        if coupon is not None and coupon.redemptions_count > 0:
            coupon.redemptions_count -= 1
        await self.session.flush()
        return True

    async def _audit(self, action: str, coupon: Coupon) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="coupon",
            entity_id=coupon.id,
            tenant_id=self.tenant.id,
            after={
                "code": coupon.code,
                "kind": coupon.kind,
                "status": coupon.status,
                "percent_bps": coupon.percent_bps,
                "amount_cents": coupon.amount_cents,
                "max_redemptions": coupon.max_redemptions,
            },
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )


# Everything a store may set on a coupon (its code is fixed once created).
EDITABLE = frozenset(
    {
        "kind",
        "percent_bps",
        "amount_cents",
        "min_subtotal_cents",
        "max_discount_cents",
        "starts_at",
        "ends_at",
        "max_redemptions",
        "per_customer_limit",
        "status",
        "note",
    }
)


def _apply(coupon: Coupon, changes: Mapping[str, Any]) -> None:
    for key, value in changes.items():
        if key in EDITABLE:
            setattr(coupon, key, value)


ACTIVE_STATUSES = frozenset({CouponStatus.ACTIVE})
