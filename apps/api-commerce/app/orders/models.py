"""Orders, their lines (snapshots of what was sold) and the status history.

An order row is written only by `OrderService.place` (ADR 0011, E11-02 test). Lines copy name,
SKU, options, modifiers and prices at the time of the sale: the catalog may change, the order
does not. Money in integer cents, quantities in thousandths of the unit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
)
from app.orders.state_machine import FulfillmentStatus, OrderStatus, RefundStatus


class Order(UUIDPrimaryKeyMixin, TimestampMixin, ActorStampMixin, TenantScoped, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "number", name="uq_orders_number"),
        # The same Idempotency-Key (per origin and customer) always means the same order.
        UniqueConstraint("tenant_id", "idempotency_hash", name="uq_orders_idempotency"),
        UniqueConstraint("tenant_id", "id", name="uq_orders_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_orders_tenant"),
        ForeignKeyConstraint(["customer_id"], ["customers.id"], name="fk_orders_customer"),
        Index("ix_orders_customer", "customer_id", "tenant_id", "id"),
        Index("ix_orders_status", "tenant_id", "status", "id"),
        Index("ix_orders_expiry", "status", "expires_at"),
    )

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=OrderStatus.AWAITING_PAYMENT
    )
    # storefront | panel | agent_llm | agent_typebot
    origin: Mapped[str] = mapped_column(String(24), nullable=False)
    channel_refs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cart_id: Mapped[str | None] = mapped_column(String(36))  # no FK: carts are purged
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    delivery_fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    # pickup | delivery | none (tickets, services, digital goods)
    fulfillment_type: Mapped[str] = mapped_column(String(16), nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=FulfillmentStatus.PENDING
    )
    # Pickup place, or address + zone {id, name, fee}: a copy, the settings may change.
    fulfillment: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    scheduled_start: Mapped[datetime | None] = mapped_column(UtcDateTime)
    scheduled_end: Mapped[datetime | None] = mapped_column(UtcDateTime)
    customer_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    consents: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    notes_customer: Mapped[str | None] = mapped_column(String(500))
    # Payment deadline: past it, the expiry job fails the order and frees its stock.
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    placed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    ready_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    shipped_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancelled_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    failed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(200))
    cancelled_by_actor: Mapped[str | None] = mapped_column(String(120))
    refund_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RefundStatus.NONE
    )
    refunded_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    risk_flags: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OrderItem(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "order_id", "line_no", name="uq_order_items_line"),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_order_items_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_order_items_variant",
        ),
        Index("ix_order_items_variant", "tenant_id", "variant_id"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    line_no: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    product_id: Mapped[str] = mapped_column(String(36), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    product_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    sku: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    option_values: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # [{group_id, group_name, modifier_id, name, price_cents}]
    modifiers: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    # {event_id, lot_id, lot_name, starts_at, venue_name} for tickets
    event: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    sold_by: Mapped[str] = mapped_column(String(8), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(16), nullable=False)
    stock_policy: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_unit_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    compare_at_cents: Mapped[int | None] = mapped_column(BigInteger)
    modifiers_unit_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_cost_cents: Mapped[int | None] = mapped_column(BigInteger)


class OrderStatusHistory(UUIDPrimaryKeyMixin, TenantScoped, Base):
    __tablename__ = "order_status_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_order_status_history_order",
        ),
        Index("ix_order_status_history_order", "tenant_id", "order_id", "occurred_at"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24), nullable=False)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    # customer | operator | system | webhook | agent
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    details: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
