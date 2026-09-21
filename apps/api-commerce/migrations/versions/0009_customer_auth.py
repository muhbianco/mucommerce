"""customers: Google sign-in flows, tenant-scoped sessions, access requests

Additive only:
- `customer_auth_flows` (new, global): one row per sign-in attempt (state, PKCE verifier,
  nonce, browser binding, handoff code; secrets as SHA-256 except the short-lived verifier);
- `customer_sessions`: tenant index (the table becomes TenantScoped) and `revoked_reason`;
- `customer_tenant_access`: the FK to `tenants` that 0002 left out, plus request/decision
  columns (`requested_at`, `request_message`, `status_changed_at`, `status_changed_by_actor`).

Revision ID: 0009_customer_auth
Revises: 0008_unimplemented_flags_off
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0009_customer_auth"
down_revision: str | None = "0008_unimplemented_flags_off"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "customer_auth_flows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("host", sa.String(253), nullable=False),
        sa.Column("return_to", sa.String(512), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("code_verifier", sa.String(128), nullable=False),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("binding_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", dt(), nullable=False),
        sa.Column("state_consumed_at", dt(), nullable=True),
        sa.Column("customer_id", sa.String(36), nullable=True),
        sa.Column("handoff_hash", sa.String(64), nullable=True),
        sa.Column("handoff_expires_at", dt(), nullable=True),
        sa.Column("handoff_consumed_at", dt(), nullable=True),
        sa.Column("terms_version", sa.String(32), nullable=True),
        sa.Column("privacy_version", sa.String(32), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_customer_auth_flows_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_auth_flows_customer_id_customers"
        ),
        sa.UniqueConstraint("state_hash", name="uq_customer_auth_flows_state_hash"),
        sa.UniqueConstraint("handoff_hash", name="uq_customer_auth_flows_handoff_hash"),
    )
    op.create_index("ix_customer_auth_flows_tenant_id", "customer_auth_flows", ["tenant_id"])

    with op.batch_alter_table("customer_sessions") as batch:
        batch.add_column(sa.Column("revoked_reason", sa.String(32), nullable=True))
        batch.create_index("ix_customer_sessions_tenant_id", ["tenant_id"])

    with op.batch_alter_table("customer_tenant_access") as batch:
        batch.add_column(sa.Column("requested_at", dt(), nullable=True))
        batch.add_column(sa.Column("request_message", sa.String(500), nullable=True))
        batch.add_column(sa.Column("status_changed_at", dt(), nullable=True))
        batch.add_column(sa.Column("status_changed_by_actor", sa.String(120), nullable=True))
        batch.create_foreign_key(
            "fk_customer_tenant_access_tenant", "tenants", ["tenant_id"], ["id"]
        )


def downgrade() -> None:
    # MariaDB will not drop an index a foreign key relies on (error 1553): tables go whole, and
    # the index that now backs a FK is dropped with the FK and the FK recreated as in 0008.
    with op.batch_alter_table("customer_tenant_access") as batch:
        batch.drop_constraint("fk_customer_tenant_access_tenant", type_="foreignkey")
        batch.drop_column("status_changed_by_actor")
        batch.drop_column("status_changed_at")
        batch.drop_column("request_message")
        batch.drop_column("requested_at")

    with op.batch_alter_table("customer_sessions") as batch:
        batch.drop_constraint("fk_customer_sessions_tenant_id_tenants", type_="foreignkey")
        batch.drop_index("ix_customer_sessions_tenant_id")
        batch.create_foreign_key(
            "fk_customer_sessions_tenant_id_tenants", "tenants", ["tenant_id"], ["id"]
        )
        batch.drop_column("revoked_reason")

    op.drop_table("customer_auth_flows")
