"""platform: tenants, domains, settings, flags, sequences, credentials, outbox, idempotency, audit

Revision ID: 0001_platform
Revises:
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_platform"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    """DATETIME(6) on MariaDB, DateTime elsewhere (SQLite in tests)."""
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("public_key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("legal_name", sa.String(200), nullable=True),
        sa.Column("document", sa.String(20), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False),
        sa.Column("default_currency", sa.String(3), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("locale", sa.String(10), nullable=False),
        sa.Column("activated_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
        sa.UniqueConstraint("public_key", name="uq_tenants_public_key"),
    )

    op.create_table(
        "tenant_domains",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("hostname", sa.String(253), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("verification_token", sa.String(43), nullable=False),
        sa.Column("verified_at", dt(), nullable=True),
        sa.Column("tls_status", sa.String(16), nullable=False),
        sa.Column("last_check_at", dt(), nullable=True),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("failed_checks", sa.Integer(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_domains_tenant_id_tenants"
        ),
        sa.UniqueConstraint("hostname", name="uq_tenant_domains_hostname"),
    )
    op.create_index(
        "ix_tenant_domains_tenant_purpose_role", "tenant_domains", ["tenant_id", "purpose", "role"]
    )

    op.create_table(
        "tenant_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_settings_key"),
    )
    op.create_index("ix_tenant_settings_tenant_id", "tenant_settings", ["tenant_id"])

    op.create_table(
        "tenant_feature_flags",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_tenant_feature_flags_key"),
    )
    op.create_index("ix_tenant_feature_flags_tenant_id", "tenant_feature_flags", ["tenant_id"])

    op.create_table(
        "tenant_sequences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("next_value", sa.Integer(), nullable=False),
        sa.UniqueConstraint("tenant_id", "name", name="uq_tenant_sequences_name"),
    )
    op.create_index("ix_tenant_sequences_tenant_id", "tenant_sequences", ["tenant_id"])

    op.create_table(
        "tenant_integration_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("key_name", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(4096), nullable=False),
        sa.Column("nonce", sa.LargeBinary(12), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("masked", sa.String(64), nullable=False),
        sa.Column("rotated_at", dt(), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "provider", "key_name", name="uq_tenant_integration_credentials_key"
        ),
    )
    op.create_index(
        "ix_tenant_integration_credentials_tenant_id",
        "tenant_integration_credentials",
        ["tenant_id"],
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", dt(), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("causation_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", dt(), nullable=True),
        sa.Column("last_error", sa.String(1000), nullable=True),
    )
    op.create_index("ix_outbox_events_pending", "outbox_events", ["status", "next_attempt_at"])
    op.create_index(
        "ix_outbox_events_aggregate",
        "outbox_events",
        ["aggregate_type", "aggregate_id", "sequence"],
    )

    op.create_table(
        "outbox_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("consumer", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", dt(), nullable=True),
        sa.Column("last_error", sa.String(1000), nullable=True),
        sa.Column("processed_at", dt(), nullable=True),
        sa.UniqueConstraint("event_id", "consumer", name="uq_outbox_deliveries_event_consumer"),
    )
    op.create_index("ix_outbox_deliveries_due", "outbox_deliveries", ["status", "next_attempt_at"])

    op.create_table(
        "processed_events",
        sa.Column("consumer", sa.String(64), primary_key=True),
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("processed_at", dt(), nullable=False),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("idem_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=True),
        sa.Column("response_body", sa.JSON(), nullable=True),
        sa.Column("locked_at", dt(), nullable=True),
        sa.Column("expires_at", dt(), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.UniqueConstraint("scope", "tenant_id", "idem_key", name="uq_idempotency_keys_key"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("actor", sa.String(120), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(48), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=True),
        sa.Column("before_json", sa.JSON(), nullable=True),
        sa.Column("after_json", sa.JSON(), nullable=True),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=True),
        sa.Column("occurred_at", dt(), nullable=False),
    )
    op.create_index(
        "ix_audit_log_entity", "audit_log", ["tenant_id", "entity_type", "entity_id", "occurred_at"]
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor", "occurred_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("idempotency_keys")
    op.drop_table("processed_events")
    op.drop_table("outbox_deliveries")
    op.drop_table("outbox_events")
    op.drop_table("tenant_integration_credentials")
    op.drop_table("tenant_sequences")
    op.drop_table("tenant_feature_flags")
    op.drop_table("tenant_settings")
    op.drop_table("tenant_domains")
    op.drop_table("tenants")
