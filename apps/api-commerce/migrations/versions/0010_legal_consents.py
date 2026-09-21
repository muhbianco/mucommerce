"""customers: store legal documents (versioned, immutable) and customer consents (LGPD)

Additive: two new TenantScoped tables. `consents.document_id` has a composite FK to
(`legal_documents.tenant_id`, `legal_documents.id`), so a consent can only point at a document
of its own store.

Revision ID: 0010_legal_consents
Revises: 0009_customer_auth
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0010_legal_consents"
down_revision: str | None = "0009_customer_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def dt() -> sa.types.TypeEngine[object]:
    return sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def long_text() -> sa.types.TypeEngine[str]:
    return sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", long_text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("published_at", dt(), nullable=False),
        sa.Column("created_by_actor", sa.String(120), nullable=False),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_legal_documents_tenant"),
        sa.UniqueConstraint("tenant_id", "kind", "version", name="uq_legal_documents_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_legal_documents_tenant_row"),
    )
    op.create_index("ix_legal_documents_tenant_id", "legal_documents", ["tenant_id"])

    op.create_table(
        "consents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("customer_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("document_version", sa.Integer(), nullable=False),
        sa.Column("accepted_at", dt(), nullable=False),
        sa.Column("revoked_at", dt(), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("ip", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("evidence_ref", sa.String(120), nullable=True),
        sa.Column("created_at", dt(), nullable=False),
        sa.Column("updated_at", dt(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consents_tenant"),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name="fk_consents_customer_id_customers"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["legal_documents.tenant_id", "legal_documents.id"],
            name="fk_consents_document",
        ),
    )
    op.create_index("ix_consents_tenant_id", "consents", ["tenant_id"])
    op.create_index("ix_consents_customer", "consents", ["tenant_id", "customer_id", "kind"])


def downgrade() -> None:
    # Whole tables: MariaDB will not drop an index a foreign key relies on (error 1553).
    op.drop_table("consents")
    op.drop_table("legal_documents")
