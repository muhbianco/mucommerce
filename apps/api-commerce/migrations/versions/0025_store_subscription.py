"""tenants.subscription_ref and tenants.billing_grace_until (fase 1: loja como serviço)

Additive only. `subscription_ref` identifies the api-agents subscription that pays for this
store (`<service_code>:<user_id>`, stable across retries of the same purchase): unique, so a
retry adopts the store it already created instead of making a second one.
`billing_grace_until` is how long a suspended store keeps serving its storefront (3 days by the
owner's decision) before the 503.

Revision ID: 0025_store_subscription
Revises: 0024_coupons
Create Date: 2026-09-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0025_store_subscription"
down_revision: str | None = "0024_coupons"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("subscription_ref", sa.String(100), nullable=True))
        batch.add_column(sa.Column("billing_grace_until", dt(), nullable=True))
        batch.create_unique_constraint("uq_tenants_subscription_ref", ["subscription_ref"])


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_constraint("uq_tenants_subscription_ref", type_="unique")
        batch.drop_column("billing_grace_until")
        batch.drop_column("subscription_ref")
