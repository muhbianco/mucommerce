"""Payment webhooks: store first (deduplicated), process by asking the provider.

`ingest` runs in the request: the store comes only from the URL key, the signature is checked
with that store's secret, the hint is parsed and inserted into the inbox — a delivery seen
before conflicts on the unique key and is answered 200 without a new row. `process` runs after
the commit (a task, inline when there is no broker, and the beat's sweep for anything left): it
claims the row with a short lease, finds the payment the hint points at and syncs it with the
provider using the store's own credentials. A forged or stale notification therefore never
approves anything by itself, and no lock is held while the provider answers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.base import utcnow
from app.payments import registry
from app.payments.config_service import PaymentConfigService
from app.payments.models import InboxStatus, Payment, PaymentWebhookInbox, TenantPaymentConfig
from app.payments.provider import InboundWebhook, WebhookHint, WebhookVerdict
from app.payments.service import PaymentService
from app.tenancy.context import CROSS_TENANT_OPTION, TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

logger = get_logger(__name__)
MAX_ATTEMPTS = 5
LEASE = timedelta(minutes=2)
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=15), timedelta(hours=1))
BATCH = 100
# Only these headers are kept (for support); never authorization or cookies.
KEPT_HEADERS = ("x-request-id", "x-signature", "content-type", "user-agent")
ACTOR = Actor.system("payment-webhooks")


@dataclass(frozen=True, slots=True)
class InboxResult:
    status: str  # received | duplicate | invalid | ignored
    inbox_id: str | None


async def ingest(
    session: AsyncSession, tenant: TenantContext, provider_name: str, inbound: InboundWebhook
) -> InboxResult:
    """Store one delivery. The caller commits (and then processes `inbox_id`)."""
    provider = registry.get_provider(provider_name)
    if provider is None:
        raise NotFoundError()
    creds = await PaymentConfigService(session, tenant, ACTOR).credentials(provider_name)
    verdict = provider.verify_webhook(creds, inbound)
    now = utcnow()
    body_hash = hashlib.sha256(inbound.raw_body).hexdigest()
    config = await session.scalar(
        select(TenantPaymentConfig).where(TenantPaymentConfig.provider == provider_name)
    )
    if config is not None:
        config.last_webhook_at = now
        config.last_webhook_valid = verdict != WebhookVerdict.INVALID
    headers = {k: v[:200] for k, v in inbound.headers.items() if k in KEPT_HEADERS}
    row = PaymentWebhookInbox(
        provider=provider_name,
        dedupe_key=f"body:{body_hash}",
        signature_valid=None
        if verdict == WebhookVerdict.UNSUPPORTED
        else verdict == WebhookVerdict.VALID,
        headers=headers,
        body_sha256=body_hash,
        received_at=now,
    )
    if verdict == WebhookVerdict.INVALID:
        row.status = InboxStatus.INVALID
        row.processed_at = now
        return await _insert(session, row)
    hint = _parse(provider_name, inbound)
    body = _json_object(inbound.raw_body)
    row.body = body
    if hint is None:
        row.status = InboxStatus.IGNORED
        row.processed_at = now
        row.result_detail = "unreadable body"
        return await _insert(session, row)
    row.dedupe_key = hint.dedupe_key[:160]
    row.event_type = (hint.event_type or "")[:64] or None
    row.resource_id = (hint.resource_id or "")[:64] or None
    if hint.provider_payment_id:
        row.payment_id = await session.scalar(
            select(Payment.id)
            .where(Payment.provider == provider_name)
            .where(Payment.provider_payment_id == hint.provider_payment_id)
        )
    row.status = InboxStatus.RECEIVED
    row.next_attempt_at = now
    return await _insert(session, row)


def _parse(provider_name: str, inbound: InboundWebhook) -> WebhookHint | None:
    provider = registry.get_provider(provider_name)
    assert provider is not None
    try:
        return provider.parse_webhook(inbound)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def _json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


async def _insert(session: AsyncSession, row: PaymentWebhookInbox) -> InboxResult:
    try:
        async with session.begin_nested():
            session.add(row)
    except IntegrityError:
        return InboxResult("duplicate", None)
    return InboxResult(row.status, row.id)


# ------------------------------------------------------------------------------- processing
async def process(session: AsyncSession, inbox_id: str) -> str:
    """Handle one inbox row; returns its new status (or `busy`/`not_due`). Commits."""
    now = utcnow()
    row = await session.scalar(
        select(PaymentWebhookInbox)
        .where(PaymentWebhookInbox.id == inbox_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True, **{CROSS_TENANT_OPTION: True})
    )
    if row is None:
        return "busy"
    if (
        row.status not in (InboxStatus.RECEIVED, InboxStatus.FAILED)
        or row.next_attempt_at is None
        or row.next_attempt_at > now
    ):
        return "not_due"
    # Claim with a lease: another worker skips it until the lease ends; a crash retries it.
    row.attempts += 1
    row.next_attempt_at = now + LEASE
    tenant_id = row.tenant_id
    await session.commit()

    tenant = await TenantResolver(session).resolve_by_id(tenant_id)
    bind_session_tenant(session, tenant_id)
    try:
        status, detail, payment_id = await _handle(session, tenant, row)
        await session.commit()  # releases the order/payment locks before the inbox row's
    except Exception as exc:
        # The row stays failed and is retried with backoff; the error is logged, not swallowed.
        await session.rollback()
        logger.exception("Payment webhook processing failed", extra={"inbox_id": inbox_id})
        status, detail, payment_id = InboxStatus.FAILED, type(exc).__name__, None
    row = await session.get(
        PaymentWebhookInbox, inbox_id, with_for_update=True, populate_existing=True
    )
    assert row is not None
    row.status = status
    row.result_detail = (detail or "")[:500] or None
    if payment_id:
        row.payment_id = payment_id
    if status == InboxStatus.FAILED:
        row.next_attempt_at = (
            utcnow() + BACKOFF[min(row.attempts - 1, len(BACKOFF) - 1)]
            if row.attempts < MAX_ATTEMPTS
            else None
        )
    else:
        row.processed_at = utcnow()
        row.next_attempt_at = None
    await session.commit()
    return status


async def _handle(
    session: AsyncSession, tenant: TenantContext, row: PaymentWebhookInbox
) -> tuple[str, str | None, str | None]:
    if registry.get_provider(row.provider) is None:
        return InboxStatus.IGNORED, "provider not allowed", None
    service = PaymentService(session, tenant, ACTOR)
    payment_id = row.payment_id
    if payment_id is None and row.resource_id:
        payment_id = await service.find_by_provider_id(row.provider, row.resource_id)
    if payment_id is None:
        return InboxStatus.IGNORED, "no payment of ours", None
    outcome = await service.sync(payment_id, source="webhook")
    if outcome == "failed":  # the provider could not be asked (timeout, 5xx): retry later
        return InboxStatus.FAILED, "provider unavailable", payment_id
    return InboxStatus.PROCESSED, outcome, payment_id


async def due(session: AsyncSession, now: datetime) -> list[str]:
    stmt = (
        select(PaymentWebhookInbox.id)
        .where(PaymentWebhookInbox.status.in_((InboxStatus.RECEIVED, InboxStatus.FAILED)))
        .where(PaymentWebhookInbox.next_attempt_at <= now)
        .order_by(PaymentWebhookInbox.next_attempt_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return list((await session.execute(stmt)).scalars())


async def run_process_webhooks(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """The beat's sweep: rows not processed right away (lost task, provider down, crash)."""
    async with factory() as session:
        ids = await due(session, now)
    done = 0
    for inbox_id in ids:
        async with factory() as session:
            try:
                done += await process(session, inbox_id) == InboxStatus.PROCESSED
            except Exception:
                logger.exception("Payment webhook sweep failed", extra={"inbox_id": inbox_id})
    return done
