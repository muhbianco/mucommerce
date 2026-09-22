"""What was sent to whom, and what is still waiting (ADR 0011 §11).

One row per (event, template, recipient): the unique key makes an e-mail happen once even if
the outbox delivers the same event twice. Bodies are kept for support and emptied after 30 days.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScoped, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin

LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql", "mariadb")


class DeliveryStatus(StrEnum):
    QUEUED = "queued"
    SENDING = "sending"  # claimed with a lease by the sender
    SENT = "sent"
    FAILED = "failed"  # attempts exhausted, or the transport refused it for good
    SKIPPED = "skipped"  # nobody to write to, or no transport configured


class NotificationDelivery(UUIDPrimaryKeyMixin, TimestampMixin, TenantScoped, Base):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "event_id",
            "template_key",
            "recipient",
            name="uq_notification_deliveries_once",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_notification_deliveries_tenant"
        ),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
        Index("ix_notification_deliveries_order", "tenant_id", "order_id", "created_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(36))
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body_html: Mapped[str | None] = mapped_column(LONG_TEXT)
    body_text: Mapped[str | None] = mapped_column(LONG_TEXT)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DeliveryStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    provider_message_id: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
