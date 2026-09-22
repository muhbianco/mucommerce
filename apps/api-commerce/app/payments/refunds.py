"""Refunds (ADR 0011 §12): request → (four eyes) → provider or by hand → completed.

- `request` runs under the order lock and takes the payment lock (order → payment → refund, the
  global lock order). What can still be refunded is the payment's paid amount minus every refund
  that counts (requested, approved, processing, completed): two operators refunding at once
  queue on the payment row and cannot exceed it.
- Policy refunds (customer cancelled within the store's window, late or duplicate payment) are
  approved at once. A refund asked by the store's team above `refund_four_eyes_threshold_cents`
  waits for a second person with `payments:refund_approve`.
- Provider refunds (Mercado Pago) are sent by `process` — the refund id is the provider's
  idempotency key, the row is claimed with a lease and the call happens with no transaction
  open; transient failures are retried with backoff by the sweep. External refunds
  (InfinitePay has no refund API) are done by the store in the provider's app and completed here
  with written evidence.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.metrics import REFUNDS_COMPLETED
from app.models.base import utcnow
from app.orders.models import Order
from app.orders.state_machine import RefundStatus as OrderRefundStatus
from app.payments import registry
from app.payments.config_service import PaymentConfigService
from app.payments.events import emit_payment, record_event
from app.payments.models import (
    COUNTED_REFUND_STATUSES,
    Payment,
    PaymentStatus,
    Refund,
    RefundKind,
    RefundMethod,
    RefundStatus,
)
from app.payments.provider import ProviderError, ProviderRef, RefundResult
from app.payments.status import can_transition
from app.tenancy.context import CROSS_TENANT_OPTION, TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor
from app.tenancy.settings_schemas import checkout_settings

logger = get_logger(__name__)
LEASE = timedelta(minutes=5)
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
MAX_ATTEMPTS = 6
BATCH = 50
REFUNDABLE = frozenset({PaymentStatus.APPROVED, PaymentStatus.PARTIALLY_REFUNDED})
POLICY_KINDS = frozenset(
    {RefundKind.CUSTOMER_CANCEL, RefundKind.LATE_PAYMENT, RefundKind.DUPLICATE_PAYMENT}
)
# Refunds of the order's own value (the others give back money that never paid for it).
ORDER_VALUE_KINDS = frozenset({RefundKind.CUSTOMER_CANCEL, RefundKind.OPERATOR})


class RefundTooLargeError(ConflictError):
    error_code = "refund_too_large"
    message = "O valor passa do que ainda pode ser devolvido neste pagamento."


class NothingToRefundError(ConflictError):
    error_code = "nothing_to_refund"
    message = "Não há pagamento aprovado para devolver neste pedido."


class RefundStateError(ConflictError):
    error_code = "refund_state"
    message = "Esta devolução não está na situação que permite essa ação."


class FourEyesError(PermissionDeniedError):
    error_code = "four_eyes"
    message = "Outra pessoa da loja precisa aprovar esta devolução."


class RefundService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor

    # ------------------------------------------------------------------ request
    async def request(
        self,
        order: Order,
        *,
        kind: str,
        reason: str,
        amount_cents: int | None = None,
        payment_id: str | None = None,
    ) -> Refund:
        """Ask to give money back. The caller holds the order lock. `amount_cents=None`: all
        that can still be refunded. `payment_id`: which payment (default: the one that paid)."""
        payment = await self.refundable_payment(order, payment_id)
        assert payment is not None  # required=True raises instead of returning None
        remaining = await self.remaining(payment)
        amount = remaining if amount_cents is None else amount_cents
        if amount <= 0 or remaining <= 0:
            raise NothingToRefundError()
        if amount > remaining:
            raise RefundTooLargeError(remaining_cents=remaining)
        provider = registry.get_provider(payment.provider)
        method = (
            RefundMethod.PROVIDER
            if provider is not None and provider.capabilities.refunds
            else RefundMethod.EXTERNAL
        )
        threshold = checkout_settings(self.tenant.settings).refund_four_eyes_threshold_cents
        needs_second = kind not in POLICY_KINDS and amount > threshold
        now = utcnow()
        refund = Refund(
            order_id=order.id,
            payment_id=payment.id,
            amount_cents=amount,
            reason=(reason or kind)[:200],
            kind=kind,
            method=method,
            status=RefundStatus.REQUESTED if needs_second else RefundStatus.APPROVED,
            requested_by_actor=self.actor.id,
            approved_by_actor=None if needs_second else self.actor.id,
            requested_at=now,
            approved_at=None if needs_second else now,
            next_attempt_at=now if not needs_second and method == RefundMethod.PROVIDER else None,
        )
        self.session.add(refund)
        await self.session.flush()
        await self._audit("refund.requested", refund, {"status": refund.status})
        await self._emit(refund, "refund.requested")
        if refund.status == RefundStatus.APPROVED:
            await self._emit(refund, "refund.approved")
        return refund

    async def remaining(self, payment: Payment) -> int:
        counted = await self.session.scalar(
            select(func.coalesce(func.sum(Refund.amount_cents), 0))
            .where(Refund.payment_id == payment.id)
            .where(Refund.status.in_(COUNTED_REFUND_STATUSES))
        )
        return (payment.paid_amount_cents or 0) - int(counted or 0)

    async def refundable_payment(
        self, order: Order, payment_id: str | None = None, *, required: bool = True
    ) -> Payment | None:
        """The payment money can still come back from, locked. Callers that will cancel the
        order take this first: the lock order is order → payment → coupon → balances."""
        stmt = select(Payment).where(Payment.order_id == order.id)
        if payment_id is not None:
            stmt = stmt.where(Payment.id == payment_id)
        else:
            stmt = stmt.where(Payment.status.in_(REFUNDABLE)).order_by(Payment.approved_at)
        payment = await self.session.scalar(
            stmt.limit(1).with_for_update().execution_options(populate_existing=True)
        )
        if payment is None or payment.status not in REFUNDABLE:
            if required:
                raise NothingToRefundError()
            return None
        return payment

    # ------------------------------------------------------------------ decisions
    async def approve(self, refund_id: str) -> Refund:
        refund = await self._locked(refund_id)
        if refund.status != RefundStatus.REQUESTED:
            raise RefundStateError(status=refund.status)
        if refund.requested_by_actor == self.actor.id:
            raise FourEyesError()
        refund.status = RefundStatus.APPROVED
        refund.approved_by_actor = self.actor.id
        refund.approved_at = utcnow()
        refund.next_attempt_at = utcnow() if refund.method == RefundMethod.PROVIDER else None
        refund.version += 1
        await self.session.flush()
        await self._audit("refund.approved", refund, {"status": refund.status})
        await self._emit(refund, "refund.approved")
        return refund

    async def reject(self, refund_id: str, reason: str) -> Refund:
        refund = await self._locked(refund_id)
        if refund.status != RefundStatus.REQUESTED:
            raise RefundStateError(status=refund.status)
        refund.status = RefundStatus.REJECTED
        refund.rejected_by_actor = self.actor.id
        refund.rejection_reason = reason[:200]
        refund.next_attempt_at = None
        refund.version += 1
        await self.session.flush()
        await self._audit("refund.rejected", refund, {"reason": refund.rejection_reason})
        await self._emit(refund, "refund.rejected")
        return refund

    async def complete_external(self, refund_id: str, evidence: str) -> Refund:
        """The store gave the money back in the provider's app; the evidence says how."""
        text = (evidence or "").strip()
        if len(text) < 10:
            raise ValidationError("Descreva a devolução (mínimo 10 caracteres).", field="evidence")
        refund = await self._locked(refund_id)
        if refund.method != RefundMethod.EXTERNAL or refund.status != RefundStatus.APPROVED:
            raise RefundStateError(status=refund.status, method=refund.method)
        refund.external_evidence = text[:500]
        refund.completed_by_actor = self.actor.id
        await self._completed(refund, provider_refund_id=None)
        return refund

    # ------------------------------------------------------------------ provider
    async def process(self, refund_id: str, now: datetime | None = None) -> str:
        """Send one approved provider refund. Commits (claim, then outcome); safe to repeat.
        `now` is the sweep's clock (what it listed as due)."""
        now = now or utcnow()
        refund = await self.session.scalar(
            select(Refund)
            .where(Refund.id == refund_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if refund is None:
            return "busy"
        due = refund.next_attempt_at is not None and refund.next_attempt_at <= now
        claimable = refund.status == RefundStatus.APPROVED or (
            refund.status == RefundStatus.PROCESSING  # lease ran out: the worker died
        )
        if refund.method != RefundMethod.PROVIDER or not claimable or not due:
            return "not_due"
        refund.status = RefundStatus.PROCESSING
        refund.attempts += 1
        claimed = refund.attempts
        # The lease starts now, not when the sweep listed its batch: a long batch must not hand
        # out leases that are already expired.
        refund.next_attempt_at = utcnow() + LEASE
        payment = await self.session.get(Payment, refund.payment_id)
        assert payment is not None
        provider = registry.get_provider(payment.provider)
        creds = await PaymentConfigService(self.session, self.tenant, self.actor).credentials(
            payment.provider
        )
        ref = ProviderRef(
            payment.provider_payment_id,
            payment.provider_reference,
            payment.provider_hints or {},
            payment.amount_cents,
        )
        amount, idempotency = refund.amount_cents, refund.id
        await self.session.commit()  # claimed; nothing held while the provider answers

        result: RefundResult | None = None
        error: ProviderError | TimeoutError | None = None
        started = time.monotonic()
        if provider is None:
            error = ProviderError("provider no longer allowed", code="provider_disabled")
        else:
            try:
                async with asyncio.timeout(settings.payments_http_timeout_seconds * 2):
                    result = await provider.refund(creds, ref, amount, idempotency=idempotency)
            except (ProviderError, TimeoutError) as exc:
                error = exc
        duration = int((time.monotonic() - started) * 1000)
        logger.info(
            "Payment provider refund",
            extra={
                "provider": payment.provider,
                "refund_id": refund_id,
                "duration_ms": duration,
                "outcome": type(error).__name__ if error else (result.status if result else None),
            },
        )

        await self._lock_order(refund.order_id)
        await self._lock_payment(refund.payment_id)
        refund = await self._lock_refund(refund_id)
        if refund.status != RefundStatus.PROCESSING or refund.attempts != claimed:
            # Somebody else claimed it while the provider answered (an expired lease, a retry
            # of this task). They own the outcome; counting it here would refund it twice.
            logger.warning("Refund claim lost while sending", extra={"refund_id": refund_id})
            return "superseded"
        if result is not None and result.status == "completed":
            await self._completed(refund, provider_refund_id=result.provider_refund_id)
            return RefundStatus.COMPLETED
        if result is not None and result.status == "pending":
            refund.provider_refund_id = result.provider_refund_id or refund.provider_refund_id
            refund.status = RefundStatus.APPROVED  # asked again: the same key returns the same one
            refund.next_attempt_at = utcnow() + BACKOFF[1]
            await self.session.flush()
            return "pending"
        definitive = (result is not None and result.status == "failed") or (
            isinstance(error, ProviderError) and error.definitive
        )
        message = (result.detail if result else None) or (str(error) if error else "failed")
        if definitive or refund.attempts >= MAX_ATTEMPTS:
            await self._failed(refund, message)
            return RefundStatus.FAILED
        refund.status = RefundStatus.APPROVED
        refund.failure_message = message[:300]
        refund.next_attempt_at = utcnow() + BACKOFF[min(refund.attempts - 1, len(BACKOFF) - 1)]
        await self.session.flush()
        return "retry"

    # ------------------------------------------------------------------ outcomes
    async def _completed(self, refund: Refund, *, provider_refund_id: str | None) -> None:
        """Caller holds order → payment → refund locks."""
        now = utcnow()
        refund.status = RefundStatus.COMPLETED
        refund.provider_refund_id = provider_refund_id or refund.provider_refund_id
        refund.completed_at = now
        refund.next_attempt_at = None
        refund.failure_message = None
        refund.version += 1
        payment = await self._lock_payment(refund.payment_id)
        payment.refunded_cents += refund.amount_cents
        target = (
            PaymentStatus.REFUNDED
            if payment.refunded_cents >= (payment.paid_amount_cents or 0)
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        if payment.status != target and can_transition(payment.status, target):
            before = payment.status
            payment.status = target
            payment.version += 1
            record_event(self.session, payment, "refund", before, target, actor_id=self.actor.id)
            await emit_payment(self.session, self.tenant.id, payment, f"payment.{target}")
        order = await self._lock_order(refund.order_id)
        if refund.kind in ORDER_VALUE_KINDS:
            order.refunded_cents += refund.amount_cents
            order.refund_status = (
                OrderRefundStatus.FULL
                if order.refunded_cents >= order.total_cents
                else OrderRefundStatus.PARTIAL
            )
        else:
            flags = dict(order.risk_flags or {})
            flags[f"{refund.kind}_refunded"] = refund.id
            order.risk_flags = flags
        await self.session.flush()
        REFUNDS_COMPLETED.labels(refund.method).inc()
        await self._audit("refund.completed", refund, {"status": refund.status})
        await self._emit(refund, "refund.completed")

    async def _failed(self, refund: Refund, message: str) -> None:
        refund.status = RefundStatus.FAILED
        refund.failure_message = message[:300]
        refund.next_attempt_at = None
        refund.version += 1
        await self.session.flush()
        logger.error(
            "Refund failed",
            extra={"refund_id": refund.id, "order_id": refund.order_id, "detail": message[:200]},
        )
        await self._audit("refund.failed", refund, {"failure": refund.failure_message})
        await self._emit(refund, "refund.failed")

    # ------------------------------------------------------------------ reads
    async def get(self, refund_id: str) -> Refund:
        refund = await self.session.get(Refund, refund_id)
        if refund is None:
            raise NotFoundError("Devolução não encontrada.")
        return refund

    async def recent(
        self, *, status: str | None, limit: int, before_id: str | None = None
    ) -> list[Refund]:
        """Newest first (ids are time-ordered UUIDv7), keyset by id; `limit + 1` rows."""
        stmt = select(Refund).order_by(Refund.id.desc()).limit(limit + 1)
        if status:
            stmt = stmt.where(Refund.status == status)
        if before_id:
            stmt = stmt.where(Refund.id < before_id)
        return list((await self.session.execute(stmt)).scalars())

    async def for_order(self, order_id: str) -> list[Refund]:
        stmt = (
            select(Refund)
            .where(Refund.order_id == order_id)
            .order_by(Refund.requested_at, Refund.id)
            .limit(50)
        )
        return list((await self.session.execute(stmt)).scalars())

    # ------------------------------------------------------------------ helpers
    async def _locked(self, refund_id: str) -> Refund:
        """order → payment → refund, found by the refund's id."""
        found = (
            await self.session.execute(
                select(Refund.order_id, Refund.payment_id).where(Refund.id == refund_id)
            )
        ).first()
        if found is None:
            raise NotFoundError("Devolução não encontrada.")
        order_id, payment_id = found
        await self._lock_order(order_id)
        await self._lock_payment(payment_id)
        return await self._lock_refund(refund_id)

    async def _lock_order(self, order_id: str) -> Order:
        order = await self.session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert order is not None
        return order

    async def _lock_payment(self, payment_id: str) -> Payment:
        payment = await self.session.scalar(
            select(Payment)
            .where(Payment.id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert payment is not None
        return payment

    async def _lock_refund(self, refund_id: str) -> Refund:
        refund = await self.session.scalar(
            select(Refund)
            .where(Refund.id == refund_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert refund is not None
        return refund

    async def _audit(self, action: str, refund: Refund, after: dict[str, Any]) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="refund",
            entity_id=refund.id,
            tenant_id=self.tenant.id,
            after={
                "order_id": refund.order_id,
                "payment_id": refund.payment_id,
                "amount_cents": refund.amount_cents,
                "kind": refund.kind,
                "method": refund.method,
                **after,
            },
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    async def _emit(self, refund: Refund, event_type: str) -> None:
        await emit(
            self.session,
            aggregate_type="refund",
            aggregate_id=refund.id,
            event_type=event_type,
            payload={
                "refund_id": refund.id,
                "order_id": refund.order_id,
                "payment_id": refund.payment_id,
                "amount_cents": refund.amount_cents,
                "kind": refund.kind,
                "method": refund.method,
                "status": refund.status,
            },
            tenant_id=self.tenant.id,
        )


# ---------------------------------------------------------------------------------- jobs
ACTOR = Actor.system("refunds")


async def due_refunds(session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    stmt = (
        select(Refund.id, Refund.tenant_id)
        .where(Refund.method == RefundMethod.PROVIDER)
        .where(Refund.status.in_((RefundStatus.APPROVED, RefundStatus.PROCESSING)))
        .where(Refund.next_attempt_at <= now)
        .order_by(Refund.next_attempt_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return [(rid, tid) for rid, tid in (await session.execute(stmt)).all()]


async def refund_tenant(session: AsyncSession, refund_id: str) -> str | None:
    tenant_id: str | None = await session.scalar(
        select(Refund.tenant_id)
        .where(Refund.id == refund_id)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return tenant_id


async def process_one(
    session: AsyncSession, refund_id: str, tenant_id: str, now: datetime | None = None
) -> str:
    tenant = await TenantResolver(session).resolve_by_id(tenant_id)
    bind_session_tenant(session, tenant_id)
    outcome = await RefundService(session, tenant, ACTOR).process(refund_id, now)
    await session.commit()
    return outcome


async def run_process_refunds(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with factory() as session:
        due = await due_refunds(session, now)
    done = 0
    for refund_id, tenant_id in due:
        async with factory() as session:
            try:
                outcome = await process_one(session, refund_id, tenant_id, now)
                done += outcome == RefundStatus.COMPLETED
            except Exception:
                logger.exception("Refund processing failed", extra={"refund_id": refund_id})
    return done
