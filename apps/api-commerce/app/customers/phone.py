"""WhatsApp phone confirmation for store customers, by reverse confirmation (no OTP message).

1. The customer types their number; we store a challenge (SHA-256 of a random token, 10 min)
   and give back a wa.me link to the official MuhBianco number with "CONFIRMAR <token>".
2. The customer sends it. api-agents receives the message on the official number and, when the
   token is not one of its own, asks `confirm` here with the number it really came from.
3. Token and number must match: the customer's phone becomes verified. Proof of possession is
   the message itself, so a verified number moves to whoever proves it last.

No message is ever sent to the customer's number, so no template approval or opt-in is needed.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar
from urllib.parse import quote

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.config import settings
from app.core.exceptions import (
    DomainError,
    ExternalServiceError,
    FeatureDisabledError,
    RateLimitedError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.phone import br_phone_variants, normalize_br_phone, same_br_phone
from app.customers.models import CustomerPhoneChallenge
from app.identity.models import Customer
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.repository import TenantRepository

logger = get_logger(__name__)

PHONE_FLAG = "customer_phone_otp"
CHALLENGE_TTL = timedelta(minutes=10)
CONFIRM_PREFIX = "CONFIRMAR"
MAX_PER_WINDOW = 3
WINDOW = timedelta(minutes=15)
ENTRY_PATH = "/api/v1/internal/commerce/whatsapp-entry"


class PhoneUnavailableError(DomainError):
    status_code = 503
    error_code = "phone_unavailable"
    message = "A confirmação por WhatsApp não está disponível agora. Tente em instantes."


@dataclass(frozen=True, slots=True)
class PhoneStart:
    whatsapp_url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PhoneConfirmed:
    tenant_name: str


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def official_whatsapp_number() -> str:
    """The official number customers write to (api-agents picks an active sender)."""
    token = settings.internal_token_agents.get_secret_value()
    if not token:
        raise PhoneUnavailableError()
    url = settings.muhbianco_accounts_internal_url.rstrip("/") + ENTRY_PATH
    try:
        async with httpx.AsyncClient(timeout=settings.muhbianco_accounts_timeout_seconds) as client:
            response = await client.get(url, headers={"X-Internal-Token": token})
    except httpx.HTTPError as exc:
        logger.warning(
            "api-agents unreachable (whatsapp entry)", extra={"error": type(exc).__name__}
        )
        raise PhoneUnavailableError() from exc
    if response.status_code != 200:
        logger.warning("api-agents whatsapp entry failed", extra={"status": response.status_code})
        raise PhoneUnavailableError()
    try:
        phone = str(response.json()["phone"])
    except (ValueError, KeyError, TypeError) as exc:
        raise ExternalServiceError("Resposta inesperada do WhatsApp oficial.") from exc
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        raise PhoneUnavailableError()
    return digits


class PhoneVerificationService:
    TOKEN_BYTES: ClassVar[int] = 9  # 12 url-safe characters, easy to read in the chat

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self, tenant: TenantContext, customer_id: str, raw_phone: str, *, ip: str | None
    ) -> PhoneStart:
        if not tenant.feature(PHONE_FLAG):
            raise FeatureDisabledError(features=[PHONE_FLAG])
        phone = normalize_br_phone(raw_phone)
        if phone is None:
            raise ValidationError("Informe um celular com DDD.", fields=["phone"])
        # End what the request did so far (session lookup) so no DB transaction stays open
        # while api-agents answers.
        await self.session.commit()
        official = await official_whatsapp_number()
        now = utcnow()
        recent = (
            await self.session.execute(
                select(func.count(CustomerPhoneChallenge.id))
                .where(CustomerPhoneChallenge.customer_id == customer_id)
                .where(CustomerPhoneChallenge.created_at > now - WINDOW)
            )
        ).scalar_one()
        if recent >= MAX_PER_WINDOW:
            raise RateLimitedError(
                retry_after_seconds=int(WINDOW.total_seconds()), limit=MAX_PER_WINDOW
            )

        # Only the newest challenge of a customer is valid.
        await self.session.execute(
            update(CustomerPhoneChallenge)
            .where(CustomerPhoneChallenge.customer_id == customer_id)
            .where(CustomerPhoneChallenge.consumed_at.is_(None))
            .values(expires_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        token = secrets.token_urlsafe(self.TOKEN_BYTES)
        row = CustomerPhoneChallenge(
            tenant_id=tenant.id,
            customer_id=customer_id,
            phone_e164=phone,
            token_hash=_sha(token),
            expires_at=now + CHALLENGE_TTL,
            ip=ip,
        )
        self.session.add(row)
        await self.session.flush()
        text = quote(f"{CONFIRM_PREFIX} {token}")
        return PhoneStart(
            whatsapp_url=f"https://wa.me/{official}?text={text}", expires_at=row.expires_at
        )

    async def confirm(self, token: str, inbound_phone: str) -> PhoneConfirmed | None:
        """Called by api-agents. None = not ours, spent, expired or sent from another number."""
        now = utcnow()
        row = (
            await self.session.execute(
                select(CustomerPhoneChallenge).where(
                    CustomerPhoneChallenge.token_hash == _sha(token)
                )
            )
        ).scalar_one_or_none()
        if (
            row is None
            or row.consumed_at is not None
            or row.expires_at <= now
            or not same_br_phone(row.phone_e164, inbound_phone)
        ):
            return None
        spent = await self.session.execute(
            update(CustomerPhoneChallenge)
            .where(CustomerPhoneChallenge.id == row.id)
            .where(CustomerPhoneChallenge.consumed_at.is_(None))
            .values(consumed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if spent.rowcount != 1:  # type: ignore[attr-defined]
            return None  # a concurrent delivery of the same message won

        customer = await self.session.get(Customer, row.customer_id)
        if customer is None:
            return None
        # The number was proven by whoever sent the message: it leaves any other customer.
        previous = (
            (
                await self.session.execute(
                    select(Customer)
                    .where(Customer.id != customer.id)
                    .where(Customer.phone_verified_at.is_not(None))
                    .where(Customer.phone_e164.in_(sorted(br_phone_variants(row.phone_e164))))
                )
            )
            .scalars()
            .all()
        )
        for other in previous:
            other.phone_verified_at = None
        customer.phone_e164 = row.phone_e164
        customer.phone_verified_at = now
        await self.session.flush()

        tenant = await TenantRepository(self.session).get(row.tenant_id)
        await audit(
            self.session,
            actor=f"customer:{customer.id}",
            action="customer.phone_verified",
            entity_type="customer",
            entity_id=customer.id,
            tenant_id=row.tenant_id,
            after={"moved_from": [o.id for o in previous]} if previous else None,
        )
        await emit(
            self.session,
            aggregate_type="customer",
            aggregate_id=customer.id,
            event_type="customer.phone_verified",
            payload={"customer_id": customer.id},
            tenant_id=row.tenant_id,
        )
        return PhoneConfirmed(tenant_name=tenant.name if tenant else "a loja")
