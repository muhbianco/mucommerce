"""Carts: one active cart per customer per store; items point at variants (prices are never
stored — every read prices the cart again through PricingService)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin

MAX_CART_LINES = 50
MAX_LINE_UNITS = 999


class CartStatus(StrEnum):
    ACTIVE = "active"
    CONVERTED = "converted"  # became an order
    ABANDONED = "abandoned"


class Cart(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "carts"
    __table_args__ = (
        # One active cart per customer and store: the column is the customer's id while the
        # cart is active and NULL otherwise (NULLs never collide in a unique index).
        UniqueConstraint("tenant_id", "active_customer_id", name="uq_carts_active"),
        UniqueConstraint("tenant_id", "id", name="uq_carts_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_carts_tenant"),
        ForeignKeyConstraint(["customer_id"], ["customers.id"], name="fk_carts_customer"),
        Index("ix_carts_customer", "customer_id", "tenant_id", "status"),
        Index("ix_carts_activity", "status", "last_activity_at"),
    )

    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    active_customer_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=CartStatus.ACTIVE)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # {type, pickup_location_id, address_id, slot_date, slot_start}; validated on every quote.
    fulfillment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    converted_order_id: Mapped[str | None] = mapped_column(String(36))
    last_activity_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class CartItem(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "cart_items"
    __table_args__ = (
        # Same variant + same modifiers = same line (adding again adds to it). Also covers the
        # composite FK to carts (tenant_id, cart_id leftmost).
        UniqueConstraint("tenant_id", "cart_id", "line_key", name="uq_cart_items_line"),
        ForeignKeyConstraint(
            ["tenant_id", "cart_id"], ["carts.tenant_id", "carts.id"], name="fk_cart_items_cart"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_cart_items_variant",
        ),
        Index("ix_cart_items_variant", "tenant_id", "variant_id"),
    )

    cart_id: Mapped[str] = mapped_column(String(36), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    line_key: Mapped[str] = mapped_column(String(64), nullable=False)
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    modifier_ids: Mapped[list[str] | None] = mapped_column(JSON)
    # Shown when the product is gone (archived) so the customer knows what to remove.
    name_snapshot: Mapped[str] = mapped_column(String(400), nullable=False)
