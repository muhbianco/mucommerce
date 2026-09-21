"""Legal documents of a store (terms, privacy) and the customers' consents to them (LGPD)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin

LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


class LegalKind(StrEnum):
    TERMS = "terms"
    PRIVACY = "privacy"


class LegalDocument(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """One published version. Never edited: a change is a new version (consents point here)."""

    __tablename__ = "legal_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "kind", "version", name="uq_legal_documents_version"),
        UniqueConstraint("tenant_id", "id", name="uq_legal_documents_tenant_row"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_legal_documents_tenant"),
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(LONG_TEXT, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    created_by_actor: Mapped[str] = mapped_column(String(120), nullable=False)


class Consent(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    """A customer accepted a document version of this store (evidence: when, where from)."""

    __tablename__ = "consents"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_consents_tenant"),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["legal_documents.tenant_id", "legal_documents.id"],
            name="fk_consents_document",
        ),
        Index("ix_consents_customer", "tenant_id", "customer_id", "kind"),
    )

    customer_id: Mapped[str] = mapped_column(String(36), ForeignKey("customers.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    document_version: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="web")
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    evidence_ref: Mapped[str | None] = mapped_column(String(120))
