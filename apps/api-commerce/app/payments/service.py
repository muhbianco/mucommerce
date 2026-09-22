"""PaymentService: a payment for an order, from creation to the provider's final word.

Creation runs in three phases (ADR 0011 §9):
A. under the order lock: the order must await payment, no other payment may be active (also a
   unique key); the payment row is written `pending` and committed;
B. no transaction open, no locks: the provider is called (a timeout leaves the payment pending —
   reconciliation finds out what happened; the payment id is the provider's idempotency key);
C. under the order lock again: the provider's answer is applied.

Every later look at the provider (webhook, reconciliation, expiry, "I already paid") follows the
same shape through `sync`: read, commit, call, lock order → payment, apply.

`apply` is the only place a payment changes status on the provider's word, and it is
idempotent: the same truth applied twice changes nothing, a status never goes backwards. An
approval confirms the order in the same transaction (stock committed,
OrderService.confirm_payment); an approval for an order no longer awaiting payment is kept and
flagged (late or duplicate payment).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    IdempotencyKeyReusedError,
    NotFoundError,
    OrderNotPayableError,
    PaymentInProgressError,
    PaymentNotCancellableError,
    ProviderNotEnabledError,
)
from app.core.logging import get_logger
from app.identity.models import Customer
from app.models.base import utcnow
from app.orders.models import Order
from app.orders.service import OrderService
from app.orders.state_machine import OrderStatus
from app.payments import registry
from app.payments.closing import close_active_payment
from app.payments.config_service import PaymentConfigService
from app.payments.events import emit_payment, record_event
from app.payments.models import ACTIVE_PAYMENT_STATUSES, Payment, PaymentStatus
from app.payments.provider import (
    CardInput,
    ChargeRequest,
    ChargeResult,
    PaymentMethod,
    PaymentProvider,
    ProviderError,
    ProviderRef,
)
from app.payments.status import CLOSED, can_transition
from app.tenancy.context import CROSS_TENANT_OPTION, TenantContext
from app.tenancy.service import Actor

logger = get_logger(__name__)
# Reconciliation cadence for a payment waiting on the customer: soon, then less often.
CHECK_BACKOFF = (timedelta(minutes=1), timedelta(minutes=3), timedelta(minutes=10))
# How long an order with an open payment waits past its deadline for the provider's last word.
GRACE = timedelta(minutes=5)
MAX_ACTIVE_CHECKS = 50
MAX_CANCEL_ATTEMPTS = 5
GAVE_UP = frozenset({PaymentStatus.REJECTED, PaymentStatus.CANCELLED, PaymentStatus.EXPIRED})
SyncOutcome = Literal["changed", "unchanged", "failed", "skipped"]


@dataclass(frozen=True, slots=True)
class PaymentCreate:
    provider: str
    method: PaymentMethod
    card: CardInput | None = None
    payer_identification: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class CallOutcome:
    result: ChargeResult | None
    duration_ms: int
    error: ProviderError | TimeoutError | None = None

    @property
    def refused(self) -> bool:
        return isinstance(self.error, ProviderError) and self.error.definitive


def payment_idempotency_hash(customer_id: str, key: str) -> str:
    return hashlib.sha256(f"payment:{customer_id}:{key}".encode()).hexdigest()


def provider_reference(tenant: TenantContext, order: Order, payment_id: str) -> str:
    return f"{tenant.slug[:24]}-{order.number}-{payment_id[-6:]}"


def next_check(attempts: int, now: datetime) -> datetime:
    return now + CHECK_BACKOFF[min(attempts, len(CHECK_BACKOFF) - 1)]


def provider_ref(payment: Payment) -> ProviderRef:
    return ProviderRef(
        payment.provider_payment_id, payment.provider_reference, payment.provider_hints or {}
    )


class PaymentService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.configs = PaymentConfigService(session, tenant, actor)

    # ------------------------------------------------------------------ options
    async def options(self) -> list[registry.PaymentOption]:
        configs = await self.configs.configs()
        secrets = {c.provider: set(await self.configs.store.status(c.provider)) for c in configs}
        return registry.options_for(self.tenant, configs, secrets)

    # ------------------------------------------------------------------ create
    async def create(
        self, order_id: str, customer_id: str, data: PaymentCreate, idempotency_key: str
    ) -> Payment:
        """Start paying an order. Commits: the payment row is durable before the provider is
        called, so a crash in between leaves a pending payment reconciliation resolves."""
        ihash = payment_idempotency_hash(customer_id, idempotency_key)
        payment, charge = await self._phase_a(order_id, customer_id, data, ihash)
        if not charge:
            return payment  # a replay of a payment the provider already answered
        provider = self._provider(payment)
        creds = await self.configs.credentials(payment.provider)
        request = await self._charge_request(payment, data)
        await self.session.commit()
        outcome = await self._call(
            "create_charge", payment.provider, provider.create_charge(creds, request), payment.id
        )
        order = await self._lock_order(payment.order_id)
        payment = await self._lock_payment(payment.id)
        self._call_event(payment, "create_charge", outcome)
        result = outcome.result
        if result is None and outcome.refused:
            assert isinstance(outcome.error, ProviderError)
            result = ChargeResult(
                status=PaymentStatus.REJECTED,
                provider_payment_id=None,
                failure_code=outcome.error.code or "provider_refused",
                failure_message=str(outcome.error),
                http_status=outcome.error.http_status,
            )
        if result is not None:
            await self.apply(payment, result, source="create", order=order)
        return payment

    async def _phase_a(
        self, order_id: str, customer_id: str, data: PaymentCreate, ihash: str
    ) -> tuple[Payment, bool]:
        order = await self.session.scalar(
            select(Order)
            .where(Order.id == order_id)
            .where(Order.customer_id == customer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if order is None:
            raise NotFoundError("Pedido não encontrado.")
        existing = await self.session.scalar(
            select(Payment).where(Payment.idempotency_hash == ihash)
        )
        if existing is not None:
            if existing.provider != data.provider or existing.method != data.method:
                raise IdempotencyKeyReusedError()
            # The first attempt never heard back: ask again with the same payment id (the
            # provider deduplicates it), but only while the order still takes this payment.
            retry = (
                existing.status == PaymentStatus.PENDING
                and existing.provider_payment_id is None
                and existing.active_order_id == order.id
                and order.status == OrderStatus.AWAITING_PAYMENT
            )
            return existing, retry
        if order.status != OrderStatus.AWAITING_PAYMENT:
            raise OrderNotPayableError(status=order.status)
        active = await self.session.scalar(
            select(Payment).where(Payment.active_order_id == order.id)
        )
        if active is not None:
            raise PaymentInProgressError(payment_id=active.id)
        options = {o.provider: o for o in await self.options()}
        option = options.get(data.provider)
        if option is None or data.method not in option.methods:
            raise ProviderNotEnabledError(provider=data.provider, method=data.method)
        if data.method == "card" and data.card is None:
            raise ProviderNotEnabledError(provider=data.provider, method=data.method)
        provider = self._provider_named(data.provider)
        now = utcnow()
        payment = Payment(
            order_id=order.id,
            active_order_id=order.id,
            provider=data.provider,
            mode=provider.capabilities.mode,
            method=data.method,
            status=PaymentStatus.PENDING,
            currency=order.currency,
            amount_cents=order.total_cents,
            installments=data.card.installments if data.card else 1,
            provider_reference="pending",
            idempotency_hash=ihash,
            expires_at=order.expires_at,
            next_check_at=now + CHECK_BACKOFF[0],
        )
        self.session.add(payment)
        await self.session.flush()
        payment.provider_reference = provider_reference(self.tenant, order, payment.id)
        self._event(payment, "created", None, PaymentStatus.PENDING)
        await self.session.flush()
        await emit_payment(self.session, self.tenant.id, payment, "payment.created")
        return payment, True

    async def _charge_request(self, payment: Payment, data: PaymentCreate) -> ChargeRequest:
        order = await self.session.get(Order, payment.order_id)
        assert order is not None
        customer = await self.session.get(Customer, order.customer_id)
        return ChargeRequest(
            payment_id=payment.id,
            provider_reference=payment.provider_reference,
            order_number=order.number,
            amount_cents=payment.amount_cents,
            currency=payment.currency,
            method=data.method,
            description=f"Pedido #{order.number} — {self.tenant.name}"[:120],
            payer_email=customer.email_normalized if customer else None,
            payer_name=(order.customer_snapshot or {}).get("name"),
            expires_at=payment.expires_at,
            notification_url=self.configs.webhook_url(payment.provider),
            card=data.card,
            payer_identification=data.payer_identification,
        )

    # ------------------------------------------------------------------ apply
    async def apply(
        self, payment: Payment, result: ChargeResult, *, source: str, order: Order | None = None
    ) -> bool:
        """Bring the payment to the provider's truth. Returns whether its status changed.
        The caller holds the order lock (order → payment, the global lock order)."""
        if result.provider_payment_id and not payment.provider_payment_id:
            if await self._provider_id_taken(payment, result.provider_payment_id):
                # One provider transaction pays one payment: a hint pointing at a transaction
                # that already paid something else (replayed or forged) changes nothing.
                logger.error(
                    "Provider payment id already used by another payment",
                    extra={"payment_id": payment.id, "provider": payment.provider},
                )
                self._event(payment, source, None, None, detail={"provider_id_conflict": True})
                await self.session.flush()
                return False
            payment.provider_payment_id = result.provider_payment_id
        if result.pix is not None:
            payment.pix_copy_paste = result.pix.copy_paste
            payment.pix_qr_base64 = result.pix.qr_base64
        if result.checkout_url:
            payment.checkout_url = result.checkout_url
        if result.expires_at is not None:
            payment.expires_at = result.expires_at
        if result.payer:
            payment.payer_snapshot = dict(result.payer)
        payment.provider_status = (result.provider_status or "")[:40] or payment.provider_status
        payment.provider_status_detail = (result.provider_status_detail or "")[
            :80
        ] or payment.provider_status_detail
        payment.raw_summary = dict(result.raw_summary) or payment.raw_summary
        target = result.status
        if target == payment.status or not can_transition(payment.status, target):
            if target != payment.status and not (payment.status in GAVE_UP and target in GAVE_UP):
                logger.warning(
                    "Payment status would go backwards; ignored",
                    extra={"payment_id": payment.id, "from": payment.status, "to": target},
                )
            await self.session.flush()
            return False
        if target == PaymentStatus.APPROVED:
            paid = result.paid_amount_cents
            if paid is not None and paid < payment.amount_cents:
                logger.error(
                    "Payment approved for less than the order total",
                    extra={"payment_id": payment.id, "paid": paid, "amount": payment.amount_cents},
                )
                self._event(payment, source, payment.status, None, detail={"underpaid": paid})
                await self.session.flush()
                return False
            payment.paid_amount_cents = paid if paid is not None else payment.amount_cents
            payment.approved_at = utcnow()
        before = payment.status
        payment.status = target
        payment.version += 1
        if target in CLOSED:
            payment.active_order_id = None
            payment.closed_at = payment.closed_at or utcnow()
            payment.next_check_at = None
        if target == PaymentStatus.REJECTED:
            payment.failure_code = (result.failure_code or "")[:64] or None
            payment.failure_message = (result.failure_message or "")[:300] or None
        self._event(payment, source, before, target, provider_status=result.provider_status)
        await self.session.flush()
        await emit_payment(self.session, self.tenant.id, payment, f"payment.{target}")
        if target == PaymentStatus.APPROVED:
            await self._approved(payment, order)
        return True

    async def _approved(self, payment: Payment, order: Order | None) -> None:
        order = order or await self._lock_order(payment.order_id)
        if order.status == OrderStatus.AWAITING_PAYMENT:
            await OrderService(self.session, self.tenant, self.actor).confirm_payment(
                order, source="payment", reason=f"{payment.provider}:{payment.method}"
            )
            # A payment the customer had given up on was paid after all: any other open one is
            # no longer needed.
            await close_active_payment(
                self.session, self.tenant.id, self.actor.id, order.id, reason="superseded"
            )
            return
        # Paid after the order failed, was cancelled, or was already paid by another payment:
        # the money is in the store's account. Flag it; the refund/recovery flow handles it.
        kind = "duplicate" if order.paid_at else "late"
        flags = dict(order.risk_flags or {})
        flags[f"{kind}_payment"] = payment.id
        order.risk_flags = flags
        await self.session.flush()
        logger.error(
            "Payment approved for an order not awaiting payment",
            extra={"payment_id": payment.id, "order_id": order.id, "order_status": order.status},
        )
        await emit_payment(
            self.session, self.tenant.id, payment, f"payment.{kind}", order_status=order.status
        )

    # ------------------------------------------------------------------ provider round trips
    async def sync(self, payment_id: str, *, source: str) -> SyncOutcome:
        """Ask the provider for the payment's current state and apply it. Commits the open
        transaction first (nothing is held while waiting for the provider); the caller commits
        the result."""
        payment = await self.session.get(Payment, payment_id, populate_existing=True)
        if payment is None or registry.get_provider(payment.provider) is None:
            return "skipped"
        provider = self._provider(payment)
        creds = await self.configs.credentials(payment.provider)
        ref = provider_ref(payment)
        await self.session.commit()
        outcome = await self._call(
            "fetch_status", payment.provider, provider.fetch_status(creds, ref), payment_id
        )
        order = await self._lock_order(payment.order_id)
        payment = await self._lock_payment(payment_id)
        if outcome.result is None:
            self._call_event(payment, "fetch_status", outcome)
            return "failed"
        result = outcome.result
        if result.provider_reference and result.provider_reference != payment.provider_reference:
            logger.error(
                "Provider answered for another reference", extra={"payment_id": payment_id}
            )
            self._event(payment, "fetch_status", None, None, detail={"reference_mismatch": True})
            return "failed"
        changed = await self.apply(payment, result, source=source, order=order)
        return "changed" if changed else "unchanged"

    async def find_by_provider_id(self, provider_name: str, provider_payment_id: str) -> str | None:
        """Our payment for a provider id — asking the provider (by our reference) when we never
        learned the id (the create answer was lost). Commits before the call."""
        found = await self.session.scalar(
            select(Payment.id)
            .where(Payment.provider == provider_name)
            .where(Payment.provider_payment_id == provider_payment_id)
        )
        if found is not None or registry.get_provider(provider_name) is None:
            return found
        provider = self._provider_named(provider_name)
        creds = await self.configs.credentials(provider_name)
        await self.session.commit()
        outcome = await self._call(
            "fetch_status",
            provider_name,
            provider.fetch_status(creds, ProviderRef(provider_payment_id, "")),
        )
        reference = outcome.result.provider_reference if outcome.result else None
        if not reference:
            return None
        matched: str | None = await self.session.scalar(
            select(Payment.id)
            .where(Payment.provider == provider_name)
            .where(Payment.provider_reference == reference)
        )
        return matched

    async def reconcile(self, payment_id: str, now: datetime) -> SyncOutcome:
        """One due check (the beat): an open payment is asked about, a payment we closed is
        cancelled at the provider. Then the next check is scheduled. The caller commits."""
        payment = await self.session.get(Payment, payment_id, populate_existing=True)
        if payment is None or payment.next_check_at is None or payment.next_check_at > now:
            return "skipped"
        outcome: SyncOutcome = "skipped"
        if payment.status in ACTIVE_PAYMENT_STATUSES:
            outcome = await self.sync(payment_id, source="reconcile")
        elif payment.status in (PaymentStatus.CANCELLED, PaymentStatus.EXPIRED):
            outcome = await self._cancel_at_provider(payment_id)
        payment = await self._lock_payment(payment_id)
        self._schedule(payment, outcome, now)
        await self.session.flush()
        return outcome

    async def _cancel_at_provider(self, payment_id: str) -> SyncOutcome:
        payment = await self.session.get(Payment, payment_id, populate_existing=True)
        provider = registry.get_provider(payment.provider) if payment else None
        if payment is None or provider is None or not provider.capabilities.cancel:
            return "skipped"
        creds = await self.configs.credentials(payment.provider)
        ref = provider_ref(payment)
        await self.session.commit()
        outcome = await self._call(
            "cancel", payment.provider, provider.cancel(creds, ref), payment_id
        )
        order = await self._lock_order(payment.order_id)
        payment = await self._lock_payment(payment_id)
        if outcome.error is not None:
            self._call_event(payment, "cancel", outcome)
            return "failed"
        if outcome.result is None:
            return "unchanged"  # nothing at the provider to cancel
        # Cancelled there too — or approved: paid before the cancel reached it (late payment).
        changed = await self.apply(payment, outcome.result, source="provider_cancel", order=order)
        return "changed" if changed else "unchanged"

    def _schedule(self, payment: Payment, outcome: SyncOutcome, now: datetime) -> None:
        if payment.status in ACTIVE_PAYMENT_STATUSES:
            payment.check_attempts += 1
            payment.next_check_at = (
                next_check(payment.check_attempts, now)
                if payment.check_attempts < MAX_ACTIVE_CHECKS
                else None
            )
        elif (
            payment.status in (PaymentStatus.CANCELLED, PaymentStatus.EXPIRED)
            and outcome == "failed"
        ):
            payment.check_attempts += 1
            payment.next_check_at = (
                next_check(payment.check_attempts, now)
                if payment.check_attempts < MAX_CANCEL_ATTEMPTS
                else None
            )
        else:
            payment.next_check_at = None
        if payment.next_check_at is None and payment.status in ACTIVE_PAYMENT_STATUSES:
            logger.error("Payment checks exhausted while open", extra={"payment_id": payment.id})
        elif payment.next_check_at is None and outcome == "failed":
            logger.error("Provider-side cancel gave up", extra={"payment_id": payment.id})

    # ------------------------------------------------------------------ customer
    async def get_for_customer(self, customer_id: str, payment_id: str) -> Payment:
        payment = await self.session.scalar(
            select(Payment)
            .join(Order, (Order.tenant_id == Payment.tenant_id) & (Order.id == Payment.order_id))
            .where(Payment.id == payment_id)
            .where(Order.customer_id == customer_id)
        )
        if payment is None:
            raise NotFoundError("Pagamento não encontrado.")
        return payment

    async def latest_for_order(self, order_id: str) -> Payment | None:
        """The open payment, else the most recent one (what the order page shows)."""
        active = await self.session.scalar(
            select(Payment).where(Payment.active_order_id == order_id)
        )
        if active is not None:
            return active
        latest: Payment | None = await self.session.scalar(
            select(Payment)
            .where(Payment.order_id == order_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(1)
        )
        return latest

    async def cancel_for_customer(self, customer_id: str, payment_id: str) -> Payment:
        """The customer gives up on this payment (to pay another way). Idempotent."""
        payment = await self.get_for_customer(customer_id, payment_id)
        await self._lock_order(payment.order_id)
        payment = await self._lock_payment(payment_id)
        if payment.status in GAVE_UP:
            return payment
        if payment.status not in ACTIVE_PAYMENT_STATUSES:
            raise PaymentNotCancellableError(status=payment.status)
        closed = await close_active_payment(
            self.session,
            self.tenant.id,
            self.actor.id,
            payment.order_id,
            reason="customer_cancelled",
            payment_id=payment.id,
        )
        assert closed is not None
        return closed

    # ------------------------------------------------------------------ helpers
    def _provider(self, payment: Payment) -> PaymentProvider:
        return self._provider_named(payment.provider)

    @staticmethod
    def _provider_named(name: str) -> PaymentProvider:
        provider = registry.get_provider(name)
        if provider is None:
            raise ProviderNotEnabledError(provider=name)
        return provider

    async def _call(
        self,
        operation: str,
        provider_name: str,
        call: Awaitable[ChargeResult | None],
        payment_id: str | None = None,
    ) -> CallOutcome:
        """One provider round trip, bounded and logged (provider, operation, duration, outcome).
        Known failures become an outcome; programming errors propagate."""
        started = time.monotonic()
        result: ChargeResult | None = None
        error: ProviderError | TimeoutError | None = None
        try:
            async with asyncio.timeout(settings.payments_http_timeout_seconds * 2):
                result = await call
        except (ProviderError, TimeoutError) as exc:
            error = exc
        duration = int((time.monotonic() - started) * 1000)
        extra = {
            "provider": provider_name,
            "operation": operation,
            "payment_id": payment_id,
            "duration_ms": duration,
            "outcome": type(error).__name__ if error else (result.status if result else "none"),
            "http_status": getattr(error, "http_status", None)
            or (result.http_status if result else None),
        }
        if error is not None:
            logger.warning("Payment provider call failed", extra=extra)
        else:
            logger.info("Payment provider call", extra=extra)
        return CallOutcome(result, duration, error)

    def _call_event(self, payment: Payment, operation: str, outcome: CallOutcome) -> None:
        error = outcome.error
        self._event(
            payment,
            "provider_call",
            None,
            None,
            provider_status=outcome.result.provider_status if outcome.result else None,
            http_status=getattr(error, "http_status", None)
            or (outcome.result.http_status if outcome.result else None),
            duration_ms=outcome.duration_ms,
            detail={
                "operation": operation,
                **(
                    {"error": type(error).__name__, "code": getattr(error, "code", None)}
                    if error
                    else {}
                ),
            },
        )

    async def _provider_id_taken(self, payment: Payment, provider_payment_id: str) -> bool:
        """Across stores on purpose (the unique key is global): answers only yes or no."""
        other = await self.session.scalar(
            select(Payment.id)
            .where(Payment.provider == payment.provider)
            .where(Payment.provider_payment_id == provider_payment_id)
            .where(Payment.id != payment.id)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        return other is not None

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

    def _event(
        self,
        payment: Payment,
        kind: str,
        before: str | None,
        after: str | None,
        *,
        provider_status: str | None = None,
        http_status: int | None = None,
        duration_ms: int | None = None,
        detail: dict[str, object] | None = None,
    ) -> None:
        record_event(
            self.session,
            payment,
            kind,
            before,
            after,
            actor_id=self.actor.id,
            provider_status=provider_status,
            http_status=http_status,
            duration_ms=duration_ms,
            detail=detail,
        )
