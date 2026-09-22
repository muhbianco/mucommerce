"""catalog: product modifier groups (paid extras with min/max per group)

Additive only: `products.modifier_groups` (JSON, nullable), ignored by older code.

Revision ID: 0015_product_modifiers
Revises: 0014_product_options
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_product_modifiers"
down_revision: str | None = "0014_product_options"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("modifier_groups", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("modifier_groups")
