"""orders: orders, order_items, order_status_history, inventory_reservations

Additive only (new tables). An order is written by OrderService.place only; its lines are
snapshots of the sale. Reservations hold stock while the order waits for payment. Every FK has
an index with its columns leftmost; composite FKs keep children inside the tenant.

Revision ID: 0020_orders
Revises: 0019_carts
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0020_orders"
down_revision: str | None = "0019_carts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def _stamps(actors: bool = False) -> list[sa.Column[object]]:
    columns = [
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
    ]
    if actors:
        columns += [
            sa.Column("created_by_actor", sa.String(120), nullable=False),
            sa.Column("updated_by_actor", sa.String(120), nullable=False),
        ]
    return columns


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("origin", sa.String(24), nullable=False),
        sa.Column("channel_refs", sa.JSON(), nullable=True),
        sa.Column("cart_id", sa.String(36), nullable=True),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal_cents", sa.BigInteger(), nullable=False),
        sa.Column("discount_cents", sa.BigInteger(), nullable=False),
        sa.Column("delivery_fee_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("coupon_code", sa.String(40), nullable=True),
        sa.Column("fulfillment_type", sa.String(16), nullable=False),
        sa.Column("fulfillment_status", sa.String(24), nullable=False),
        sa.Column("fulfillment", sa.JSON(), nullable=True),
        sa.Column("scheduled_start", dt(), nullable=True),
        sa.Column("scheduled_end", dt(), nullable=True),
        sa.Column("customer_snapshot", sa.JSON(), nullable=False),
        sa.Column("consents", sa.JSON(), nullable=True),
        sa.Column("notes_customer", sa.String(500), nullable=True),
        sa.Column("expires_at", dt(), nullable=True),
        sa.Column("placed_at", dt(), nullable=False),
        sa.Column("paid_at", dt(), nullable=True),
        sa.Column("accepted_at", dt(), nullable=True),
        sa.Column("ready_at", dt(), nullable=True),
        sa.Column("shipped_at", dt(), nullable=True),
        sa.Column("delivered_at", dt(), nullable=True),
        sa.Column("cancelled_at", dt(), nullable=True),
        sa.Column("failed_at", dt(), nullable=True),
        sa.Column("cancel_reason", sa.String(200), nullable=True),
        sa.Column("cancelled_by_actor", sa.String(120), nullable=True),
        sa.Column("refund_status", sa.String(16), nullable=False),
        sa.Column("refunded_cents", sa.BigInteger(), nullable=False),
        sa.Column("risk_flags", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *_stamps(actors=True),
        sa.UniqueConstraint("tenant_id", "number", name="uq_orders_number"),
        sa.UniqueConstraint("tenant_id", "idempotency_hash", name="uq_orders_idempotency"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_orders_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_orders_tenant"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name="fk_orders_customer"),
    )
    op.create_index("ix_orders_tenant_id", "orders", ["tenant_id"])
    op.create_index("ix_orders_customer", "orders", ["customer_id", "tenant_id", "id"])
    op.create_index("ix_orders_status", "orders", ["tenant_id", "status", "id"])
    op.create_index("ix_orders_expiry", "orders", ["status", "expires_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("line_no", sa.SmallInteger(), nullable=False),
        sa.Column("product_id", sa.String(36), nullable=False),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("product_kind", sa.String(16), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("variant_name", sa.String(200), nullable=False),
        sa.Column("option_values", sa.JSON(), nullable=True),
        sa.Column("modifiers", sa.JSON(), nullable=True),
        sa.Column("event", sa.JSON(), nullable=True),
        sa.Column("sold_by", sa.String(8), nullable=False),
        sa.Column("unit_label", sa.String(16), nullable=False),
        sa.Column("stock_policy", sa.String(16), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("base_unit_cents", sa.BigInteger(), nullable=False),
        sa.Column("compare_at_cents", sa.BigInteger(), nullable=True),
        sa.Column("modifiers_unit_cents", sa.BigInteger(), nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger(), nullable=False),
        sa.Column("subtotal_cents", sa.BigInteger(), nullable=False),
        sa.Column("discount_cents", sa.BigInteger(), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost_cents", sa.BigInteger(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("tenant_id", "order_id", "line_no", name="uq_order_items_line"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_order_items_order",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_order_items_variant",
        ),
    )
    op.create_index("ix_order_items_tenant_id", "order_items", ["tenant_id"])
    op.create_index("ix_order_items_variant", "order_items", ["tenant_id", "variant_id"])

    op.create_table(
        "order_status_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("occurred_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_order_status_history_order",
        ),
    )
    op.create_index("ix_order_status_history_tenant_id", "order_status_history", ["tenant_id"])
    op.create_index(
        "ix_order_status_history_order",
        "order_status_history",
        ["tenant_id", "order_id", "occurred_at"],
    )

    op.create_table(
        "inventory_reservations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("committed_at", dt(), nullable=True),
        sa.Column("released_at", dt(), nullable=True),
        sa.Column("release_reason", sa.String(32), nullable=True),
        *_stamps(),
        sa.UniqueConstraint(
            "tenant_id", "order_id", "variant_id", name="uq_inventory_reservations_line"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_inventory_reservations_order",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_inventory_reservations_variant",
        ),
    )
    op.create_index("ix_inventory_reservations_tenant_id", "inventory_reservations", ["tenant_id"])
    op.create_index(
        "ix_inventory_reservations_variant",
        "inventory_reservations",
        ["tenant_id", "variant_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("inventory_reservations")
    op.drop_table("order_status_history")
    op.drop_table("order_items")
    op.drop_table("orders")
