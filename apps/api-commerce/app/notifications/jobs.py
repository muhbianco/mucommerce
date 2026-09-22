"""Sending the queued e-mails, and forgetting their bodies after a month.

Each delivery is claimed with a lease (`sending`, `next_attempt_at = now + LEASE`, SKIP LOCKED)
and committed before n8n is called, so nothing is held while the network answers and a worker
that dies only delays that one e-mail. Failures back off (1 min, 5 min, 30 min, 2 h, 12 h) and
end as `failed`; a refused payload fails at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.models.base import utcnow
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.notifications.transport import N8nTransport, TransportError
from app.tenancy.context import CROSS_TENANT_OPTION

logger = get_logger(__name__)
LEASE = timedelta(minutes=5)
BACKOFF = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=12),
)
MAX_ATTEMPTS = 6
BATCH = 100
BODY_KEPT = timedelta(days=30)


async def due(session: AsyncSession, now: datetime) -> list[str]:
    stmt = (
        select(NotificationDelivery.id)
        .where(NotificationDelivery.status.in_((DeliveryStatus.QUEUED, DeliveryStatus.SENDING)))
        .where(NotificationDelivery.next_attempt_at <= now)
        .order_by(NotificationDelivery.next_attempt_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return list((await session.execute(stmt)).scalars())


async def send_one(
    session: AsyncSession, delivery_id: str, transport: N8nTransport, now: datetime
) -> str:
    """Claim, send, record. Commits; returns the row's new status (or why it was skipped)."""
    delivery = await session.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True, **{CROSS_TENANT_OPTION: True})
    )
    if delivery is None:
        return "busy"
    if delivery.next_attempt_at is None or delivery.next_attempt_at > now:
        return "not_due"
    if delivery.status not in (DeliveryStatus.QUEUED, DeliveryStatus.SENDING):
        return delivery.status
    if not transport.configured:
        delivery.status = DeliveryStatus.SKIPPED
        delivery.next_attempt_at = None
        delivery.last_error = "sem transporte configurado"
        await session.commit()
        return DeliveryStatus.SKIPPED
    delivery.status = DeliveryStatus.SENDING
    delivery.attempts += 1
    delivery.next_attempt_at = now + LEASE
    attempts = delivery.attempts
    await session.commit()

    message_id: str | None = None
    error: TransportError | None = None
    try:
        message_id = await transport.send(delivery)
    except TransportError as exc:
        error = exc

    delivery = await session.scalar(
        select(NotificationDelivery)
        .where(NotificationDelivery.id == delivery_id)
        .with_for_update()
        .execution_options(populate_existing=True, **{CROSS_TENANT_OPTION: True})
    )
    assert delivery is not None
    if error is None:
        delivery.status = DeliveryStatus.SENT
        delivery.sent_at = utcnow()
        delivery.provider_message_id = message_id
        delivery.next_attempt_at = None
        delivery.last_error = None
    else:
        delivery.last_error = str(error)[:500]
        if error.definitive or attempts >= MAX_ATTEMPTS:
            delivery.status = DeliveryStatus.FAILED
            delivery.next_attempt_at = None
            logger.error(
                "E-mail not sent",
                extra={"delivery_id": delivery_id, "template": delivery.template_key},
            )
        else:
            delivery.status = DeliveryStatus.QUEUED
            delivery.next_attempt_at = utcnow() + BACKOFF[min(attempts - 1, len(BACKOFF) - 1)]
    status = str(delivery.status)
    await session.commit()
    return status


async def run_send_notifications(
    factory: async_sessionmaker[AsyncSession], now: datetime, transport: N8nTransport | None = None
) -> int:
    sender = transport or N8nTransport()
    async with factory() as session:
        ids = await due(session, now)
    sent = 0
    for delivery_id in ids:
        async with factory() as session:
            try:
                sent += await send_one(session, delivery_id, sender, now) == DeliveryStatus.SENT
            except Exception:
                logger.exception("E-mail sending failed", extra={"delivery_id": delivery_id})
    return sent


async def purge_notification_bodies(session: AsyncSession, now: datetime | None = None) -> int:
    """Subjects and status stay for support; the bodies go after a month."""
    cutoff = (now or utcnow()) - BODY_KEPT
    result = await session.execute(
        update(NotificationDelivery)
        .where(NotificationDelivery.created_at < cutoff)
        .where(NotificationDelivery.body_html.isnot(None))
        .values(body_html=None, body_text=None)
        .execution_options(synchronize_session=False, **{CROSS_TENANT_OPTION: True})
    )
    return int(getattr(result, "rowcount", 0) or 0)
