"""Payments: the store's provider configuration, each payment attempt, its event log and the
webhook inbox (ADR 0011, items 8 to 10).

A payment row is written before the provider is called; the provider is the truth about its
status, and a webhook only says "look again" — processing always fetches the current state.
Secrets (access tokens, webhook secrets) are not here: they live encrypted in
tenant_integration_credentials.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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

# The Pix QR code comes as a base64 PNG (tens of KB): MEDIUMTEXT on MariaDB.
LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


class PaymentStatus(StrEnum):
    PENDING = "pending"  # row written, provider not answered yet (or unknown)
    REQUIRES_ACTION = "requires_action"  # Pix shown / link opened: waiting for the customer
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    CHARGEBACK = "chargeback"


# At most one of these per order (unique on active_order_id).
ACTIVE_PAYMENT_STATUSES = frozenset({PaymentStatus.PENDING, PaymentStatus.REQUIRES_ACTION})


class TenantPaymentConfig(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "tenant_payment_configs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_tenant_payment_configs_provider"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_tenant_payment_configs_tenant"
        ),
    )

    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sandbox: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Not secret: shown to the browser (MP public key) or used in links (InfinitePay handle).
    public_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    methods: Mapped[list[str] | None] = mapped_column(JSON)
    installments_max: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    configured_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    configured_by_actor: Mapped[str | None] = mapped_column(String(120))
    last_test_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_error: Mapped[str | None] = mapped_column(String(300))
    last_webhook_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_webhook_valid: Mapped[bool | None] = mapped_column(Boolean)


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_hash", name="uq_payments_idempotency"),
        # One payment awaiting the customer per order, enforced by the database.
        UniqueConstraint("tenant_id", "active_order_id", name="uq_payments_active"),
        UniqueConstraint("provider", "provider_payment_id", name="uq_payments_provider_id"),
        UniqueConstraint("provider_reference", name="uq_payments_reference"),
        UniqueConstraint("tenant_id", "id", name="uq_payments_tenant_row"),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"], ["orders.tenant_id", "orders.id"], name="fk_payments_order"
        ),
        Index("ix_payments_order", "tenant_id", "order_id", "created_at"),
        Index("ix_payments_check", "status", "next_check_at"),
    )

    order_id: Mapped[str] = mapped_column(String(36), nullable=False)
    active_order_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # embedded | redirect
    method: Mapped[str] = mapped_column(String(24), nullable=False)  # pix | card | link
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=PaymentStatus.PENDING)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    paid_amount_cents: Mapped[int | None] = mapped_column(BigInteger)
    refunded_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    installments: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64))
    # Our reference at the provider (external_reference / order_nsu): unique everywhere.
    provider_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(40))
    provider_status_detail: Mapped[str | None] = mapped_column(String(80))
    idempotency_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pix_copy_paste: Mapped[str | None] = mapped_column(Text)
    pix_qr_base64: Mapped[str | None] = mapped_column(LONG_TEXT)
    checkout_url: Mapped[str | None] = mapped_column(String(1000))
    provider_hints: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    approved_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(300))
    # Brand, last four digits, masked document: never card data or tokens.
    payer_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    next_check_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    check_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class PaymentEvent(UUIDPrimaryKeyMixin, TenantScoped, Base):
    """What happened to a payment: provider calls (status, timing), status changes, webhooks."""

    __tablename__ = "payment_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            ["payments.tenant_id", "payments.id"],
            name="fk_payment_events_payment",
        ),
        Index("ix_payment_events_payment", "tenant_id", "payment_id", "occurred_at"),
    )

    payment_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # created | provider_call | status_changed | webhook | reconciled | refund
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    provider_status: Mapped[str | None] = mapped_column(String(40))
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)


class InboxStatus(StrEnum):
    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"  # nothing of ours (unknown payment, other account)
    INVALID = "invalid"  # signature check failed
    FAILED = "failed"  # processing failed; retried until attempts run out


class PaymentWebhookInbox(UUIDPrimaryKeyMixin, TenantScoped, Base):
    __tablename__ = "payment_webhook_inbox"
    __table_args__ = (
        # A notification delivered twice is stored once (the insert conflicts, answer 200).
        UniqueConstraint(
            "tenant_id", "provider", "dedupe_key", name="uq_payment_webhook_inbox_dedupe"
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_payment_webhook_inbox_tenant"),
        Index("ix_payment_webhook_inbox_due", "status", "next_attempt_at"),
    )

    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    event_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(64))
    payment_id: Mapped[str | None] = mapped_column(String(36))  # no FK: the hint may be forged
    signature_valid: Mapped[bool | None] = mapped_column(Boolean)
    headers: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # allow-listed only
    body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    body_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=InboxStatus.RECEIVED)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    result_detail: Mapped[str | None] = mapped_column(String(500))
