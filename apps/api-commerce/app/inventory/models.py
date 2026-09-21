"""Stock per variant: a balance row plus an append-only ledger of movements.

Quantities are integers in thousandths of the variant's unit (`*_milli`): 1 un = 1000, and a
product sold by weight in grams keeps 3 decimals of a gram. Integer sums are exact on every
database (SQLite, used by the tests, has no fixed-point type), so `SUM(qty_milli) == on_hand`
is checkable to the unit.

Costs are micro-reais per unit (`unit_cost_micro`): 1 real = 1_000_000.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKeyConstraint, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    ActorStampMixin,
    Base,
    TenantScoped,
    TimestampMixin,
    UtcDateTime,
    UUIDPrimaryKeyMixin,
    utcnow,
)

MILLI = 1000


class MovementType:
    INITIAL = "initial"
    RECEIPT = "purchase_in"
    ADJUSTMENT = "adjustment"
    LOSS = "loss"
    COUNT = "count"
    # Phase 2 (orders) writes these; declared here so the vocabulary lives in one place.
    SALE_COMMIT = "sale_commit"
    SALE_RETURN = "sale_return"


class InventoryBalance(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "variant_id", name="uq_inventory_balances_variant"),
        ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_inventory_balances_variant",
        ),
    )

    variant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    on_hand_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Held by open orders (phase 2); available = on_hand - reserved.
    reserved_milli: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    min_level_milli: Mapped[int | None] = mapped_column(BigInteger)


class StockAdjustment(UUIDPrimaryKeyMixin, ActorStampMixin, TenantScoped, Base):
    """Header of one panel operation (receipt, count, loss, manual adjustment)."""

    __tablename__ = "stock_adjustments"
    __table_args__ = (Index("ix_stock_adjustments_created", "tenant_id", "created_at"),)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(String(500))
    line_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class InventoryMovement(UUIDPrimaryKeyMixin, TenantScoped, Base):
    """Append-only: never updated or deleted. Corrections are new movements."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_inventory_movements_variant",
        ),
        Index("ix_inventory_movements_variant", "tenant_id", "variant_id", "occurred_at"),
        Index("ix_inventory_movements_reference", "tenant_id", "reference_type", "reference_id"),
    )

    variant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    qty_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_milli: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_cost_micro: Mapped[int | None] = mapped_column(BigInteger)
    reference_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(36), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
