"""cart: carts and cart_items

Additive only (new tables). One active cart per customer per store through a unique index on
(tenant_id, active_customer_id), NULL once the cart is converted or abandoned. Items reference
their cart and variant through composite FKs; prices are never stored.

Revision ID: 0019_carts
Revises: 0018_customer_addresses
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0019_carts"
down_revision: str | None = "0018_customer_addresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "carts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("active_customer_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fulfillment", sa.JSON(), nullable=True),
        sa.Column("coupon_code", sa.String(40), nullable=True),
        sa.Column("converted_order_id", sa.String(36), nullable=True),
        sa.Column("last_activity_at", dt(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "active_customer_id", name="uq_carts_active"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_carts_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_carts_tenant"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], name="fk_carts_customer"),
    )
    op.create_index("ix_carts_tenant_id", "carts", ["tenant_id"])
    op.create_index("ix_carts_customer", "carts", ["customer_id", "tenant_id", "status"])
    op.create_index("ix_carts_activity", "carts", ["status", "last_activity_at"])

    op.create_table(
        "cart_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("cart_id", sa.String(36), nullable=False),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("line_key", sa.String(64), nullable=False),
        sa.Column("quantity_milli", sa.BigInteger(), nullable=False),
        sa.Column("modifier_ids", sa.JSON(), nullable=True),
        sa.Column("name_snapshot", sa.String(400), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "cart_id", "line_key", name="uq_cart_items_line"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cart_id"], ["carts.tenant_id", "carts.id"], name="fk_cart_items_cart"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "variant_id"],
            ["product_variants.tenant_id", "product_variants.id"],
            name="fk_cart_items_variant",
        ),
    )
    op.create_index("ix_cart_items_tenant_id", "cart_items", ["tenant_id"])
    op.create_index("ix_cart_items_variant", "cart_items", ["tenant_id", "variant_id"])


def downgrade() -> None:
    op.drop_table("cart_items")
    op.drop_table("carts")
