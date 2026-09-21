"""tenancy: turn off flags that have no code behind them yet (events, pickup, chatwoot)

Data only. They defaulted to on at tenant creation, so the site admin showed modules as
enabled that do nothing. Each one is switched back on per tenant when its phase ships.
Downgrade is a no-op: the previous values carried no behaviour.

Revision ID: 0008_unimplemented_flags_off
Revises: 0007_admin_external_account
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0008_unimplemented_flags_off"
down_revision: str | None = "0007_admin_external_account"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FLAGS = ("events", "pickup", "chatwoot")


def upgrade() -> None:
    flags = sa.table(
        "tenant_feature_flags",
        sa.column("key", sa.String),
        sa.column("enabled", sa.Boolean),
        sa.column("updated_at", sa.DateTime),
    )
    op.execute(
        flags.update()
        .where(flags.c.key.in_(FLAGS), flags.c.enabled.is_(True))
        .values(enabled=False, updated_at=datetime.now(UTC).replace(tzinfo=None))
    )


def downgrade() -> None:
    pass
