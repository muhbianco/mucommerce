"""payments: tenant_payment_configs, payments, payment_events, payment_webhook_inbox

Additive only (new tables). Secrets stay in tenant_integration_credentials (encrypted). One
payment awaiting the customer per order is enforced by a unique key on active_order_id (NULL
once the payment is closed); webhooks are deduplicated by (tenant, provider, dedupe_key).

Revision ID: 0021_payments
Revises: 0020_orders
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0021_payments"
down_revision: str | None = "0020_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def stamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "tenant_payment_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("sandbox", sa.Boolean(), nullable=False),
        sa.Column("public_config", sa.JSON(), nullable=True),
        sa.Column("methods", sa.JSON(), nullable=True),
        sa.Column("installments_max", sa.Integer(), nullable=False),
        sa.Column("configured_at", dt(), nullable=True),
        sa.Column("configured_by_actor", sa.String(120), nullable=True),
        sa.Column("last_test_at", dt(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_error", sa.String(300), nullable=True),
        sa.Column("last_webhook_at", dt(), nullable=True),
        sa.Column("last_webhook_valid", sa.Boolean(), nullable=True),
        *stamps(),
        sa.UniqueConstraint("tenant_id", "provider", name="uq_tenant_payment_configs_provider"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_payment_configs_tenant"
        ),
    )
    op.create_index("ix_tenant_payment_configs_tenant_id", "tenant_payment_configs", ["tenant_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=False),
        sa.Column("active_order_id", sa.String(36), nullable=True),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("paid_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("refunded_cents", sa.BigInteger(), nullable=False),
        sa.Column("installments", sa.Integer(), nullable=False),
        sa.Column("provider_payment_id", sa.String(64), nullable=True),
        sa.Column("provider_reference", sa.String(64), nullable=False),
        sa.Column("provider_status", sa.String(40), nullable=True),
        sa.Column("provider_status_detail", sa.String(80), nullable=True),
        sa.Column("idempotency_hash", sa.String(64), nullable=False),
        sa.Column("pix_copy_paste", sa.Text(), nullable=True),
        sa.Column(
            "pix_qr_base64",
            sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb"),
            nullable=True,
        ),
        sa.Column("checkout_url", sa.String(1000), nullable=True),
        sa.Column("provider_hints", sa.JSON(), nullable=True),
        sa.Column("expires_at", dt(), nullable=True),
        sa.Column("approved_at", dt(), nullable=True),
        sa.Column("closed_at", dt(), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("failure_message", sa.String(300), nullable=True),
        sa.Column("payer_snapshot", sa.JSON(), nullable=True),
        sa.Column("raw_summary", sa.JSON(), nullable=True),
        sa.Column("next_check_at", dt(), nullable=True),
        sa.Column("check_attempts", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *stamps(),
        sa.UniqueConstraint("tenant_id", "idempotency_hash", name="uq_payments_idempotency"),
        sa.UniqueConstraint("tenant_id", "active_order_id", name="uq_payments_active"),
        sa.UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_id"),
        sa.UniqueConstraint("provider_reference", name="uq_payments_reference"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_payments_tenant_row"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"], name="fk_payments_order"
        ),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_order", "payments", ["tenant_id", "order_id", "created_at"])
    op.create_index("ix_payments_check", "payments", ["status", "next_check_at"])

    op.create_table(
        "payment_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("payment_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=True),
        sa.Column("to_status", sa.String(24), nullable=True),
        sa.Column("provider_status", sa.String(40), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("occurred_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payments.tenant_id", "payments.id"],
            name="fk_payment_events_payment",
        ),
    )
    op.create_index("ix_payment_events_tenant_id", "payment_events", ["tenant_id"])
    op.create_index(
        "ix_payment_events_payment", "payment_events", ["tenant_id", "payment_id", "occurred_at"]
    )

    op.create_table(
        "payment_webhook_inbox",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("dedupe_key", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(64), nullable=True),
        sa.Column("payment_id", sa.String(36), nullable=True),
        sa.Column("signature_valid", sa.Boolean(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=True),
        sa.Column("body", sa.JSON(), nullable=True),
        sa.Column("hints", sa.JSON(), nullable=True),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", dt(), nullable=True),
        sa.Column("received_at", dt(), nullable=False),
        sa.Column("processed_at", dt(), nullable=True),
        sa.Column("result_detail", sa.String(500), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "provider", "dedupe_key", name="uq_payment_webhook_inbox_dedupe"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_payment_webhook_inbox_tenant"
        ),
    )
    op.create_index("ix_payment_webhook_inbox_tenant_id", "payment_webhook_inbox", ["tenant_id"])
    op.create_index(
        "ix_payment_webhook_inbox_due", "payment_webhook_inbox", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_table("payment_webhook_inbox")
    op.drop_table("payment_events")
    op.drop_table("payments")
    op.drop_table("tenant_payment_configs")
