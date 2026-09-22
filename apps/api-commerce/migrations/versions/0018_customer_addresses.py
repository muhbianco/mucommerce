"""customers: delivery addresses per store

Additive only (new table), tenant-scoped; the FK to `customers` is covered by an index with
`customer_id` leftmost (MariaDB would otherwise create an implicit one).

Revision ID: 0018_customer_addresses
Revises: 0017_fulfillment_v2
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0018_customer_addresses"
down_revision: str | None = "0017_fulfillment_v2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "customer_addresses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(40), nullable=True),
        sa.Column("recipient_name", sa.String(120), nullable=False),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("postal_code", sa.String(8), nullable=False),
        sa.Column("street", sa.String(160), nullable=False),
        sa.Column("number", sa.String(20), nullable=False),
        sa.Column("complement", sa.String(80), nullable=True),
        sa.Column("district", sa.String(80), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("reference", sa.String(160), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_customer_addresses_tenant"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_addresses_customer"
        ),
    )
    op.create_index("ix_customer_addresses_tenant_id", "customer_addresses", ["tenant_id"])
    op.create_index(
        "ix_customer_addresses_customer", "customer_addresses", ["customer_id", "tenant_id"]
    )


def downgrade() -> None:
    op.drop_table("customer_addresses")
