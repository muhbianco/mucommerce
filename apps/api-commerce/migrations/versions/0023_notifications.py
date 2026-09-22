"""notification_deliveries: transactional e-mails (stage E, S14)

Additive only (a new table). One row per (event, template, recipient): the unique key makes an
e-mail happen once even when the outbox delivers the same event twice. Bodies are emptied by the
purge job after 30 days.

Revision ID: 0023_notifications
Revises: 0022_refunds
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0023_notifications"
down_revision: str | None = "0022_refunds"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def long_text() -> sa.types.TypeEngine[object]:
    return sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("template_key", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("order_id", sa.String(36), nullable=True),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body_html", long_text(), nullable=True),
        sa.Column("body_text", long_text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", dt(), nullable=True),
        sa.Column("provider_message_id", sa.String(64), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("sent_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "event_id",
            "template_key",
            "recipient",
            name="uq_notification_deliveries_once",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_notification_deliveries_tenant"
        ),
    )
    op.create_index(
        "ix_notification_deliveries_tenant_id", "notification_deliveries", ["tenant_id"]
    )
    op.create_index(
        "ix_notification_deliveries_due", "notification_deliveries", ["status", "next_attempt_at"]
    )
    op.create_index(
        "ix_notification_deliveries_order",
        "notification_deliveries",
        ["tenant_id", "order_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
