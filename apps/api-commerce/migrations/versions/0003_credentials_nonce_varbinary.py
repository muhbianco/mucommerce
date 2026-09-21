"""credentials: AES-GCM nonce as VARBINARY(12) instead of BLOB(12)

MariaDB turns BLOB(12) into TINYBLOB, so `alembic check` reported drift against the model.
A 12-byte nonce belongs inline as VARBINARY(12). The table is empty in production.

Revision ID: 0003_credentials_nonce_varbinary
Revises: 0002_identity
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003_credentials_nonce_varbinary"
down_revision: str | None = "0002_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TYPE = sa.LargeBinary(12).with_variant(mysql.VARBINARY(12), "mysql", "mariadb")
OLD_TYPE = sa.LargeBinary(12)


def upgrade() -> None:
    with op.batch_alter_table("tenant_integration_credentials") as batch:
        batch.alter_column("nonce", existing_type=OLD_TYPE, type_=NEW_TYPE, existing_nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("tenant_integration_credentials") as batch:
        batch.alter_column("nonce", existing_type=NEW_TYPE, type_=OLD_TYPE, existing_nullable=False)
