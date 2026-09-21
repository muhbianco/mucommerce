from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import (
    DeliveryStatus,
    OutboxDelivery,
    OutboxEvent,
    OutboxStatus,
    ProcessedEvent,
)
from app.core.logging import get_logger, request_id_var
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION, bind_session_tenant

logger = get_logger(__name__)

Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]

MAX_ATTEMPTS = 8
BACKOFF_SECONDS = (30, 60, 300, 900, 1800, 3600, 7200, 14400)
# A dispatched delivery is leased for this long: if its task message is lost (broker
# restart, worker killed before ack), `claim_due_deliveries` picks it up after the lease
# instead of leaving it pending forever, and never re-dispatches it while in flight.
DISPATCH_LEASE_SECONDS = 300

DispatchList = list[tuple[str, str]]  # (event_id, consumer)


def _locks_rows(session: AsyncSession) -> bool:
    """Row locks (FOR UPDATE / SKIP LOCKED) exist on MariaDB, not on SQLite (tests)."""
    return session.bind is not None and session.bind.dialect.name in {"mysql", "mariadb"}


@dataclass(frozen=True, slots=True)
class Consumer:
    name: str
    event_patterns: tuple[str, ...]
    handler: Handler

    def matches(self, event_type: str) -> bool:
        return any(fnmatch.fnmatchcase(event_type, pattern) for pattern in self.event_patterns)


@dataclass
class ConsumerRegistry:
    consumers: dict[str, Consumer] = field(default_factory=dict)

    def register(self, name: str, event_patterns: tuple[str, ...], handler: Handler) -> None:
        self.consumers[name] = Consumer(name, event_patterns, handler)

    def for_event(self, event_type: str) -> list[Consumer]:
        return [c for c in self.consumers.values() if c.matches(event_type)]

    def clear(self) -> None:
        self.consumers.clear()


registry = ConsumerRegistry()


async def emit(
    session: AsyncSession,
    *,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, Any],
    tenant_id: str | None,
    causation_id: str | None = None,
) -> OutboxEvent:
    """Write the event in the current transaction. Sequence is per aggregate."""
    seq_stmt = (
        select(func.coalesce(func.max(OutboxEvent.sequence), 0))
        .where(OutboxEvent.aggregate_type == aggregate_type)
        .where(OutboxEvent.aggregate_id == aggregate_id)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    if _locks_rows(session):
        seq_stmt = seq_stmt.with_for_update()
    last_seq = (await session.execute(seq_stmt)).scalar_one()
    request_id = request_id_var.get()
    event = OutboxEvent(
        tenant_id=tenant_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        sequence=int(last_seq) + 1,
        event_type=event_type,
        payload=payload,
        correlation_id=request_id if request_id != "-" else None,
        causation_id=causation_id,
    )
    session.add(event)
    return event


async def relay_pending(session: AsyncSession, *, limit: int = 200) -> DispatchList:
    """Create per-consumer deliveries for pending events and return what to dispatch.

    The caller must COMMIT before dispatching: a consumer task that starts before the
    commit would not see its delivery row. Each delivery is created leased, so a
    dispatch that never reaches a worker is retried by `claim_due_deliveries`.
    Concurrent relays skip each other's rows (SKIP LOCKED) instead of double-relaying.
    """
    now = utcnow()
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.status == OutboxStatus.PENDING)
        .where((OutboxEvent.next_attempt_at.is_(None)) | (OutboxEvent.next_attempt_at <= now))
        .order_by(OutboxEvent.occurred_at, OutboxEvent.sequence)
        .limit(limit)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    if _locks_rows(session):
        stmt = stmt.with_for_update(skip_locked=True)
    events = list((await session.execute(stmt)).scalars())
    lease_until = now + timedelta(seconds=DISPATCH_LEASE_SECONDS)
    to_dispatch: DispatchList = []
    for event in events:
        consumers = registry.for_event(event.event_type)
        if not consumers:
            event.status = OutboxStatus.DONE
            continue
        for consumer in consumers:
            session.add(
                OutboxDelivery(
                    event_id=event.id, consumer=consumer.name, next_attempt_at=lease_until
                )
            )
            to_dispatch.append((event.id, consumer.name))
        event.status = OutboxStatus.DISPATCHED
        event.attempts += 1
    await session.flush()
    return to_dispatch


async def deliver(session: AsyncSession, event_id: str, consumer_name: str) -> str:
    """Run one consumer for one event, exactly once. Returns the delivery status."""
    consumer = registry.consumers.get(consumer_name)
    event = await session.get(OutboxEvent, event_id)
    if consumer is None or event is None:
        return "missing"

    already = await session.get(ProcessedEvent, {"consumer": consumer_name, "event_id": event_id})
    if already is not None:
        return "duplicate"

    if event.tenant_id:
        # Consumers read tenant-scoped rows; give them the event's tenant context.
        bind_session_tenant(session, event.tenant_id)

    try:
        await consumer.handler(session, event)
        session.add(ProcessedEvent(consumer=consumer_name, event_id=event_id))
        delivery = await _get_or_create_delivery(session, event_id, consumer_name)
        delivery.status = DeliveryStatus.DONE
        delivery.processed_at = utcnow()
        delivery.attempts += 1
        await session.flush()
    except IntegrityError:
        # Another worker processed the event (or created the delivery) concurrently.
        # Nothing of ours is kept; the delivery lease re-dispatches it if still needed.
        await session.rollback()
        return "duplicate"
    except Exception as exc:  # consumer failure: drop its partial work, keep bookkeeping
        await session.rollback()
        event = await session.get(OutboxEvent, event_id)
        delivery = await _get_or_create_delivery(session, event_id, consumer_name)
        if event is None:
            return "missing"
        delivery.attempts += 1
        delivery.last_error = f"{type(exc).__name__}: {exc}"[:1000]
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_attempt_at = None
            event.status = OutboxStatus.FAILED
            event.last_error = delivery.last_error
            logger.error(
                "Outbox delivery moved to DLQ",
                extra={"event_id": event_id, "consumer": consumer_name},
            )
        else:
            backoff = BACKOFF_SECONDS[min(delivery.attempts - 1, len(BACKOFF_SECONDS) - 1)]
            delivery.next_attempt_at = utcnow() + timedelta(seconds=backoff)
            logger.warning(
                "Outbox delivery failed; will retry",
                extra={
                    "event_id": event_id,
                    "consumer": consumer_name,
                    "attempt": delivery.attempts,
                    "error": delivery.last_error,
                },
            )
        return str(delivery.status)

    await _settle_event(session, event)
    return str(delivery.status)


async def _get_or_create_delivery(
    session: AsyncSession, event_id: str, consumer_name: str
) -> OutboxDelivery:
    delivery = (
        await session.execute(
            select(OutboxDelivery)
            .where(OutboxDelivery.event_id == event_id)
            .where(OutboxDelivery.consumer == consumer_name)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
    ).scalar_one_or_none()
    if delivery is None:
        delivery = OutboxDelivery(event_id=event_id, consumer=consumer_name)
        session.add(delivery)
        await session.flush()
    return delivery


async def _settle_event(session: AsyncSession, event: OutboxEvent) -> None:
    pending = (
        await session.execute(
            select(func.count())
            .select_from(OutboxDelivery)
            .where(OutboxDelivery.event_id == event.id)
            .where(OutboxDelivery.status != DeliveryStatus.DONE)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
    ).scalar_one()
    if int(pending) == 0 and event.status != OutboxStatus.FAILED:
        event.status = OutboxStatus.DONE


async def claim_due_deliveries(session: AsyncSession, *, limit: int = 200) -> DispatchList:
    """Pending deliveries whose backoff or dispatch lease expired, claimed for dispatch.

    Covers failed attempts waiting for backoff, manual DLQ retries (`retry_failed`)
    and dispatches whose task never ran. Claiming pushes `next_attempt_at` one lease
    ahead, so the next beat tick does not dispatch the same delivery again while its
    task is in flight. As with `relay_pending`, commit before dispatching.
    """
    now = utcnow()
    stmt = (
        select(OutboxDelivery)
        .where(OutboxDelivery.status == DeliveryStatus.PENDING)
        .where(OutboxDelivery.next_attempt_at <= now)
        .order_by(OutboxDelivery.next_attempt_at)
        .limit(limit)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    if _locks_rows(session):
        stmt = stmt.with_for_update(skip_locked=True)
    due = list((await session.execute(stmt)).scalars())
    lease_until = now + timedelta(seconds=DISPATCH_LEASE_SECONDS)
    for delivery in due:
        delivery.next_attempt_at = lease_until
    await session.flush()
    return [(delivery.event_id, delivery.consumer) for delivery in due]


async def retry_failed(session: AsyncSession, event_id: str) -> int:
    """Manual DLQ retry from the ops panel: reset failed deliveries of one event."""
    result = await session.execute(
        update(OutboxDelivery)
        .where(OutboxDelivery.event_id == event_id)
        .where(OutboxDelivery.status == DeliveryStatus.FAILED)
        .values(
            status=DeliveryStatus.PENDING, attempts=0, next_attempt_at=utcnow(), last_error=None
        )
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    await session.execute(
        update(OutboxEvent)
        .where(OutboxEvent.id == event_id)
        .values(status=OutboxStatus.DISPATCHED, last_error=None)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    rowcount = getattr(result, "rowcount", 0)
    return int(rowcount or 0)
