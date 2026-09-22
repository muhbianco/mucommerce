"""refunds: money going back to customers (stage E, S13)

Additive only (a new table). Provider refunds (Mercado Pago) are sent by a worker with the
refund id as the idempotency key; external refunds (InfinitePay, done in its app) are completed
by the store with evidence. Above the store's limit a second person approves (four eyes).

Revision ID: 0022_refunds
Revises: 0021_payments
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0022_refunds"
down_revision: str | None = "0021_payments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "refunds",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("payment_id", sa.String(36), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider_refund_id", sa.String(64), nullable=True),
        sa.Column("requested_by_actor", sa.String(120), nullable=False),
        sa.Column("approved_by_actor", sa.String(120), nullable=True),
        sa.Column("rejected_by_actor", sa.String(120), nullable=True),
        sa.Column("rejection_reason", sa.String(200), nullable=True),
        sa.Column("completed_by_actor", sa.String(120), nullable=True),
        sa.Column("external_evidence", sa.String(500), nullable=True),
        sa.Column("failure_message", sa.String(300), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", dt(), nullable=True),
        sa.Column("requested_at", dt(), nullable=False),
        sa.Column("approved_at", dt(), nullable=True),
        sa.Column("completed_at", dt(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"], name="fk_refunds_order"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payments.tenant_id", "payments.id"],
            name="fk_refunds_payment",
        ),
    )
    op.create_index("ix_refunds_tenant_id", "refunds", ["tenant_id"])
    op.create_index("ix_refunds_order", "refunds", ["tenant_id", "order_id", "requested_at"])
    op.create_index("ix_refunds_payment", "refunds", ["tenant_id", "payment_id"])
    op.create_index("ix_refunds_due", "refunds", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("refunds")
