"""identity: admin_users.external_account_id (MuhBianco account, api-agents users.id)

Additive: nullable column plus a unique key. Staff sign in to the panel with their MuhBianco
account; this links the panel user to that account by id (never by e-mail alone).

Revision ID: 0007_admin_external_account
Revises: 0006_inventory
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_admin_external_account"
down_revision: str | None = "0006_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("admin_users") as batch:
        batch.add_column(sa.Column("external_account_id", sa.String(36), nullable=True))
        batch.create_unique_constraint(
            "uq_admin_users_external_account_id", ["external_account_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("admin_users") as batch:
        batch.drop_constraint("uq_admin_users_external_account_id", type_="unique")
        batch.drop_column("external_account_id")
