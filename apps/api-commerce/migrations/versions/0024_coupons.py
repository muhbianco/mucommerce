"""coupons and coupon_redemptions (stage E, S17)

Additive only. A coupon's `redemptions_count` and its redemption rows change together under the
coupon's row lock, so a limited coupon is used exactly as many times as the store allowed. A
redemption is released (kept, marked) when its order expires or is cancelled before payment.

Revision ID: 0024_coupons
Revises: 0023_notifications
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0024_coupons"
down_revision: str | None = "0023_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("percent_bps", sa.Integer(), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("min_subtotal_cents", sa.BigInteger(), nullable=False),
        sa.Column("max_discount_cents", sa.BigInteger(), nullable=True),
        sa.Column("starts_at", dt(), nullable=True),
        sa.Column("ends_at", dt(), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("per_customer_limit", sa.Integer(), nullable=True),
        sa.Column("redemptions_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_coupons_code"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_coupons_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_coupons_tenant"),
    )
    op.create_index("ix_coupons_tenant_id", "coupons", ["tenant_id"])

    op.create_table(
        "coupon_redemptions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("coupon_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("discount_cents", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("released_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "order_id", name="uq_coupon_redemptions_order"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "coupon_id"],
            ["coupons.tenant_id", "coupons.id"],
            name="fk_coupon_redemptions_coupon",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_coupon_redemptions_order",
        ),
    )
    op.create_index("ix_coupon_redemptions_tenant_id", "coupon_redemptions", ["tenant_id"])
    op.create_index(
        "ix_coupon_redemptions_coupon",
        "coupon_redemptions",
        ["tenant_id", "coupon_id", "customer_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("coupon_redemptions")
    op.drop_table("coupons")
