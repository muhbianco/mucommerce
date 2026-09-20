"""identity: admin users, memberships, refresh tokens, customers, identities, sessions, access

Revision ID: 0002_identity
Revises: 0001_platform
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_identity"
down_revision: str | None = "0001_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),
        sa.Column("google_subject", sa.String(255), nullable=True),
        sa.Column("platform_role", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_login_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
        sa.UniqueConstraint("google_subject", name="uq_admin_users_google_subject"),
    )

    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("admin_user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_memberships_tenant_id_tenants"
        ),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["admin_users.id"],
            name="fk_tenant_memberships_admin_user_id_admin_users",
        ),
        sa.UniqueConstraint("tenant_id", "admin_user_id", name="uq_tenant_memberships_user"),
    )

    op.create_table(
        "admin_refresh_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("admin_user_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", dt(), nullable=False),
        sa.Column("revoked_at", dt(), nullable=True),
        sa.Column("replaced_by_id", sa.String(36), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["admin_users.id"],
            name="fk_admin_refresh_tokens_admin_user_id_admin_users",
        ),
        sa.UniqueConstraint("token_hash", name="uq_admin_refresh_tokens_token_hash"),
    )
    op.create_index(
        "ix_admin_refresh_tokens_admin_user_id", "admin_refresh_tokens", ["admin_user_id"]
    )

    op.create_table(
        "customers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("email_normalized", sa.String(320), nullable=True),
        sa.Column("email_verified_at", dt(), nullable=True),
        sa.Column("phone_e164", sa.String(20), nullable=True),
        sa.Column("phone_verified_at", dt(), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("document_enc", sa.LargeBinary(256), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("anonymized_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("email_normalized", name="uq_customers_email_normalized"),
    )
    op.create_index("ix_customers_phone_e164", "customers", ["phone_e164"])

    op.create_table(
        "customer_identities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email_at_provider", sa.String(320), nullable=True),
        sa.Column("raw_claims", sa.JSON(), nullable=True),
        sa.Column("last_login_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_identities_customer_id_customers"
        ),
        sa.UniqueConstraint("provider", "subject", name="uq_customer_identities_sub"),
    )
    op.create_index("ix_customer_identities_customer_id", "customer_identities", ["customer_id"])

    op.create_table(
        "customer_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", dt(), nullable=False),
        sa.Column("revoked_at", dt(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_customer_sessions_customer_id_customers"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_customer_sessions_tenant_id_tenants"
        ),
        sa.UniqueConstraint("token_hash", name="uq_customer_sessions_token_hash"),
    )
    op.create_index(
        "ix_customer_sessions_customer_tenant", "customer_sessions", ["customer_id", "tenant_id"]
    )

    op.create_table(
        "customer_tenant_access",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("chatwoot_contact_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_by_actor", sa.String(120), nullable=True),
        sa.Column("approved_at", dt(), nullable=True),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_customer_tenant_access_customer_id_customers",
        ),
        sa.UniqueConstraint("tenant_id", "customer_id", name="uq_customer_tenant_access_customer"),
    )
    op.create_index("ix_customer_tenant_access_tenant_id", "customer_tenant_access", ["tenant_id"])
    op.create_index(
        "ix_customer_tenant_access_status", "customer_tenant_access", ["tenant_id", "status"]
    )


def downgrade() -> None:
    op.drop_table("customer_tenant_access")
    op.drop_table("customer_sessions")
    op.drop_table("customer_identities")
    op.drop_table("customers")
    op.drop_table("admin_refresh_tokens")
    op.drop_table("tenant_memberships")
    op.drop_table("admin_users")
