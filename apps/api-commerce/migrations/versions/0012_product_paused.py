"""catalog: paused products and variants

Additive only: `paused_at`, `paused_reason` and `paused_by_actor` on `products` and
`product_variants`. The status itself is a new value of the existing `status` column
(`paused`), which code before this revision does not list as published: it hides a paused
product instead of selling it, so a rollback of the image alone is safe.

Revision ID: 0012_product_paused
Revises: 0011_customer_phone_challenges
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0012_product_paused"
down_revision: str | None = "0011_customer_phone_challenges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("products", "product_variants")


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("paused_at", dt(), nullable=True))
            batch.add_column(sa.Column("paused_reason", sa.String(200), nullable=True))
            batch.add_column(sa.Column("paused_by_actor", sa.String(120), nullable=True))


def downgrade() -> None:
    # The older code knows no `paused`: those rows leave the storefront (inactive) rather than
    # go back on sale behind the owner's back.
    for table in _TABLES:
        rows = sa.table(table, sa.column("status", sa.String))
        op.execute(rows.update().where(rows.c.status == "paused").values(status="inactive"))
        with op.batch_alter_table(table) as batch:
            batch.drop_column("paused_by_actor")
            batch.drop_column("paused_reason")
            batch.drop_column("paused_at")
