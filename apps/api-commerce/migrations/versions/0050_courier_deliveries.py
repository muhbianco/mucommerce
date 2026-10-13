"""Corridas de entregador chamadas pela loja (Uber Direct, docs/15 F2).

Só cria. Uma linha por tentativa; a aberta tem `active_marker` = 1 e é única por pedido (o
histórico fica com NULL, que não colide) — é o banco que impede dois entregadores para o mesmo
pedido.

Revision ID: 0050_courier_deliveries
Revises: 0049_refund_shipping
Create Date: 2026-10-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0050_courier_deliveries"
down_revision: str | None = "0049_refund_shipping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "courier_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("environment", sa.String(12), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("active_marker", sa.SmallInteger(), nullable=True),
        sa.Column("requested_by_actor", sa.String(120), nullable=False),
        sa.Column("max_fee_cents", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", dt(), nullable=True),
        sa.Column("provider_delivery_id", sa.String(64), nullable=True),
        sa.Column("provider_status", sa.String(32), nullable=True),
        sa.Column("quote_id", sa.String(64), nullable=True),
        sa.Column("fee_cents", sa.BigInteger(), nullable=True),
        sa.Column("quoted_fee_cents", sa.BigInteger(), nullable=True),
        sa.Column("tracking_url", sa.String(500), nullable=True),
        sa.Column("courier_name", sa.String(80), nullable=True),
        sa.Column("courier_vehicle", sa.String(80), nullable=True),
        sa.Column("pickup_eta", dt(), nullable=True),
        sa.Column("dropoff_eta", dt(), nullable=True),
        sa.Column("last_error", sa.String(300), nullable=True),
        sa.Column("dispatched_at", dt(), nullable=True),
        sa.Column("picked_up_at", dt(), nullable=True),
        sa.Column("delivered_at", dt(), nullable=True),
        sa.Column("closed_at", dt(), nullable=True),
        sa.Column("tracked_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "order_id", "active_marker", name="uq_courier_deliveries_active"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_courier_deliveries_tenant_row"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_courier_deliveries_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            ["orders.tenant_id", "orders.id"],
            name="fk_courier_deliveries_order",
        ),
    )
    op.create_index("ix_courier_deliveries_tenant_id", "courier_deliveries", ["tenant_id"])
    op.create_index(
        "ix_courier_deliveries_order", "courier_deliveries", ["tenant_id", "order_id", "created_at"]
    )
    op.create_index(
        "ix_courier_deliveries_due", "courier_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_courier_deliveries_provider_id",
        "courier_deliveries",
        ["provider", "provider_delivery_id"],
    )


def downgrade() -> None:
    op.drop_table("courier_deliveries")
