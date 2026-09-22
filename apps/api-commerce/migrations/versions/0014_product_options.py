"""catalog: product options (the variant matrix is their product)

Additive only: `products.options` (JSON, nullable). Variants already carry `option_values`
(0004); code before this revision ignores both.

Revision ID: 0014_product_options
Revises: 0013_tags
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_product_options"
down_revision: str | None = "0013_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("options", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("products") as batch:
        batch.drop_column("options")
