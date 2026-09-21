"""customers: WhatsApp phone confirmation challenges (reverse confirmation, no OTP sent)

Additive: one global table. The customer sends "CONFIRMAR <token>" from their WhatsApp to the
official MuhBianco number; api-agents receives it and asks this API to confirm the token with
the number it came from. Global (not TenantScoped) because that call arrives with no store;
`tenant_id` is kept to know which store asked. Tokens are stored as SHA-256.

Revision ID: 0011_customer_phone_challenges
Revises: 0010_legal_consents
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0011_customer_phone_challenges"
down_revision: str | None = "0010_legal_consents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "customer_phone_challenges",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("phone_e164", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", dt(), nullable=False),
        sa.Column("consumed_at", dt(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_customer_phone_challenges_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_customer_phone_challenges_customer_id_customers",
        ),
        sa.UniqueConstraint("token_hash", name="uq_customer_phone_challenges_token_hash"),
    )
    op.create_index(
        "ix_customer_phone_challenges_customer",
        "customer_phone_challenges",
        ["customer_id", "created_at"],
    )
    op.create_index(
        "ix_customer_phone_challenges_tenant_id", "customer_phone_challenges", ["tenant_id"]
    )


def downgrade() -> None:
    # The whole table: MariaDB will not drop an index a foreign key relies on (error 1553).
    op.drop_table("customer_phone_challenges")
