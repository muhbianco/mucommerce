"""inventory: inventory_balances, stock_adjustments, inventory_movements (append-only)

Additive only. Quantities are BIGINT thousandths of the unit (`*_milli`), costs micro-reais.

Revision ID: 0006_inventory
Revises: 0005_media_assets
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0006_inventory"
down_revision: str | None = "0005_media_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def _variant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "variant_id"],
        ["product_variants.tenant_id", "product_variants.id"],
        name=f"fk_{table}_variant",
    )


def upgrade() -> None:
    op.create_table(
        "inventory_balances",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("on_hand_milli", sa.BigInteger(), nullable=False),
        sa.Column("reserved_milli", sa.BigInteger(), nullable=False),
        sa.Column("min_level_milli", sa.BigInteger(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "variant_id", name="uq_inventory_balances_variant"),
        _variant_fk("inventory_balances"),
    )
    op.create_index("ix_inventory_balances_tenant_id", "inventory_balances", ["tenant_id"])

    op.create_table(
        "stock_adjustments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("updated_by_actor", sa.String(120), nullable=False),
    )
    op.create_index("ix_stock_adjustments_tenant_id", "stock_adjustments", ["tenant_id"])
    op.create_index(
        "ix_stock_adjustments_created", "stock_adjustments", ["tenant_id", "created_at"]
    )

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("variant_id", sa.String(36), nullable=False),
        sa.Column("movement_type", sa.String(16), nullable=False),
        sa.Column("qty_milli", sa.BigInteger(), nullable=False),
        sa.Column("balance_after_milli", sa.BigInteger(), nullable=False),
        sa.Column("unit_cost_micro", sa.BigInteger(), nullable=True),
        sa.Column("reference_type", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("occurred_at", dt(), nullable=False),
        _variant_fk("inventory_movements"),
    )
    op.create_index("ix_inventory_movements_tenant_id", "inventory_movements", ["tenant_id"])
    op.create_index(
        "ix_inventory_movements_variant",
        "inventory_movements",
        ["tenant_id", "variant_id", "occurred_at"],
    )
    op.create_index(
        "ix_inventory_movements_reference",
        "inventory_movements",
        ["tenant_id", "reference_type", "reference_id"],
    )


def downgrade() -> None:
    op.drop_table("inventory_movements")
    op.drop_table("stock_adjustments")
    op.drop_table("inventory_balances")
