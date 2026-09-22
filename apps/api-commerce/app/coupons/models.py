"""Coupons and the orders that used them (ADR 0011).

A coupon's `redemptions_count` and its redemption rows change together under the coupon's row
lock, so a coupon limited to N is used N times even with everyone checking out at once. A
redemption is released (not deleted) when the order it belongs to expires or is cancelled before
payment, which frees the place for someone else and keeps the history.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)


class CouponKind(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"


class CouponStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class RedemptionStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"  # the order expired or was cancelled before payment


class Coupon(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_coupons_code"),
        UniqueConstraint("tenant_id", "id", name="uq_coupons_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_coupons_tenant"),
    )

    code: Mapped[str] = mapped_column(String(40), nullable=False)  # stored uppercase
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    percent_bps: Mapped[int | None] = mapped_column(Integer)  # 1000 = 10%
    amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    min_subtotal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    max_discount_cents: Mapped[int | None] = mapped_column(BigInteger)
    starts_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ends_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    max_redemptions: Mapped[int | None] = mapped_column(Integer)
    per_customer_limit: Mapped[int | None] = mapped_column(Integer)
    redemptions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CouponStatus.ACTIVE)
    note: Mapped[str | None] = mapped_column(String(200))


class CouponRedemption(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "coupon_redemptions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", name="uq_coupon_redemptions_order"),
        ForeignKeyConstraint(
            ["tenant_id", "coupon_id"],
            ["coupons.tenant_id", "coupons.id"],
            name="fk_coupon_redemptions_coupon",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_coupon_redemptions_order",
        ),
        Index(
            "ix_coupon_redemptions_coupon",
            "tenant_id",
            "coupon_id",
            "customer_id",
            "status",
        ),
    )

    coupon_id: Mapped[str] = mapped_column(String(36), nullable=False)
    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)  # no FK: it is the order's
    discount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RedemptionStatus.ACTIVE)
    released_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
