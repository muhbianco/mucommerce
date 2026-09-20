from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UtcDateTime, UUIDPrimaryKeyMixin, utcnow


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only. The runtime DB user only has INSERT/SELECT on this table."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "tenant_id", "entity_type", "entity_id", "occurred_at"),
        Index("ix_audit_log_actor", "actor", "occurred_at"),
    )

    tenant_id: Mapped[str | None] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(36))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    request_id: Mapped[str | None] = mapped_column(String(36))
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    DONE = "done"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class OutboxEvent(UUIDPrimaryKeyMixin, Base):
    """Domain event written in the same transaction as the aggregate change."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_pending", "status", "next_attempt_at"),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id", "sequence"),
    )

    tenant_id: Mapped[str | None] = mapped_column(String(36))
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    causation_id: Mapped[str | None] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=OutboxStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(1000))


class OutboxDelivery(UUIDPrimaryKeyMixin, Base):
    """Per-consumer delivery state of an event: retries, backoff and the DLQ live here."""

    __tablename__ = "outbox_deliveries"
    __table_args__ = (
        UniqueConstraint("event_id", "consumer", name="uq_outbox_deliveries_event_consumer"),
        Index("ix_outbox_deliveries_due", "status", "next_attempt_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), nullable=False)
    consumer: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(1000))
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class ProcessedEvent(Base):
    """Idempotency of consumers: (consumer, event_id) processed exactly once."""

    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)


class IdempotencyKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint("scope", "tenant_id", "idem_key", name="uq_idempotency_keys_key"),
    )

    scope: Mapped[str] = mapped_column(String(32), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, default="-")
    idem_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int | None] = mapped_column(SmallInteger)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    locked_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
