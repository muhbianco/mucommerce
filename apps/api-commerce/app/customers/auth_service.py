"""Customer sign-in with Google: start on the store, central callback, handoff back to the store.

1. `start` (the store's web, server-side): new flow row with hashed state/nonce/binding and the
   PKCE verifier; returns Google's authorize URL.
2. `callback` (api-commerce host, from Google): consumes the state once, commits, then talks to
   Google (no transaction held across the network), checks the id_token and nonce, re-checks the
   store, links or creates the customer and issues a 60 s one-time handoff code.
3. `complete` (the store's web again): consumes the handoff only for the same store, host and
   browser binding, and opens the session.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.config import settings
from app.core.exceptions import (
    CustomerLoginUnavailableError,
    FeatureDisabledError,
    InvalidHandoffError,
    InvalidLoginStateError,
)
from app.customers import oidc
from app.customers.legal import Acceptance, LegalService
from app.customers.models import CustomerAuthFlow
from app.customers.repository import AccessRepository, CustomerSessionRepository
from app.customers.sessions import hash_token, open_session
from app.identity.models import Customer, CustomerIdentity
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.models import DomainPurpose, DomainStatus, TenantStatus
from app.tenancy.repository import TenantRepository
from app.tenancy.resolver import TenantResolver

FLOW_TTL = timedelta(minutes=10)
HANDOFF_TTL = timedelta(seconds=60)
PROVIDER = "google"
LOGIN_FLAG = "customer_login"
# Claims worth keeping for support; nothing else from the id_token is stored.
KEPT_CLAIMS = ("name", "picture", "locale", "hd")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def safe_return_to(value: str | None) -> str:
    """Only relative paths on the same store; anything else (//evil, \\\\, schemes) → '/'."""
    if not value or len(value) > 512 or not value.startswith("/") or value.startswith("//"):
        return "/"
    if any(ch in value for ch in "\\\r\n\t") or value.startswith("/auth/"):
        return "/"
    return value


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    user, _, domain = email.partition("@")
    return f"{user[:1]}{'*' * max(len(user) - 1, 1)}@{domain}"


@dataclass(frozen=True, slots=True)
class Completed:
    session_token: str
    expires_at: datetime
    return_to: str
    customer: Customer
    access_status: str | None


class CustomerAuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------------ 1. start
    async def start(
        self,
        tenant: TenantContext,
        *,
        return_to: str | None,
        binding: str,
        ip: str | None,
        terms_version: int | None = None,
        privacy_version: int | None = None,
    ) -> str:
        if not settings.customer_login_configured:
            raise CustomerLoginUnavailableError()
        if not tenant.feature(LOGIN_FLAG):
            raise FeatureDisabledError(features=[LOGIN_FLAG])
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)  # 64 chars; PKCE allows 43..128
        self.session.add(
            CustomerAuthFlow(
                tenant_id=tenant.id,
                host=tenant.host or "",
                return_to=safe_return_to(return_to),
                state_hash=_sha(state),
                code_verifier=verifier,
                nonce_hash=_sha(nonce),
                binding_hash=_sha(binding),
                expires_at=utcnow() + FLOW_TTL,
                terms_version=str(terms_version) if terms_version else None,
                privacy_version=str(privacy_version) if privacy_version else None,
                ip=ip,
            )
        )
        await self.session.flush()
        return oidc.authorize_url(state=state, nonce=nonce, code_challenge=_challenge(verifier))

    # ------------------------------------------------------------------ 2. callback
    async def callback(self, *, state: str | None, code: str | None, error: str | None) -> str:
        """Returns where to send the browser: the store's handoff, or its /entrar with an error."""
        if not state or len(state) > 128:
            raise InvalidLoginStateError()
        now = utcnow()
        state_hash = _sha(state)
        consumed = await self.session.execute(
            update(CustomerAuthFlow)
            .where(CustomerAuthFlow.state_hash == state_hash)
            .where(CustomerAuthFlow.state_consumed_at.is_(None))
            .where(CustomerAuthFlow.expires_at > now)
            .values(state_consumed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:  # type: ignore[attr-defined]
            raise InvalidLoginStateError()
        flow = (
            await self.session.execute(
                select(CustomerAuthFlow).where(CustomerAuthFlow.state_hash == state_hash)
            )
        ).scalar_one()
        # The state is spent before any network call: a replay can never reach Google again.
        await self.session.commit()

        origin = settings.storefront_origin(flow.host)

        def fail(reason: str) -> str:
            return f"{origin}/entrar?{urlencode({'erro': reason, 'next': flow.return_to})}"

        if error or not code:
            return fail("cancelado")
        try:
            identity = await oidc.verify_id_token(
                await oidc.exchange_code(code, flow.code_verifier)
            )
        except oidc.OidcError as exc:
            return fail(exc.code)
        if identity.nonce is None or not hmac.compare_digest(_sha(identity.nonce), flow.nonce_hash):
            return fail("login_invalido")

        # The store may have changed while the customer was at Google.
        tenant = await TenantResolver(self.session).resolve_by_id(flow.tenant_id)
        domain = await TenantRepository(self.session).get_domain_by_hostname(flow.host)
        if (
            tenant.status != TenantStatus.ACTIVE
            or domain is None
            or domain.tenant_id != tenant.id
            or domain.status != DomainStatus.ACTIVE
            or domain.purpose != DomainPurpose.STOREFRONT
        ):
            return fail("loja_indisponivel")
        if not tenant.feature(LOGIN_FLAG) or not settings.customer_login_configured:
            return fail("login_indisponivel")

        now = utcnow()
        customer, created = await self._link_google(identity, now)
        if customer.status != "active" or customer.anonymized_at is not None:
            return fail("conta_indisponivel")
        handoff = secrets.token_urlsafe(32)
        flow.customer_id = customer.id
        flow.handoff_hash = _sha(handoff)
        flow.handoff_expires_at = now + HANDOFF_TTL
        if created:
            await emit(
                self.session,
                aggregate_type="customer",
                aggregate_id=customer.id,
                event_type="customer.created",
                payload={"customer_id": customer.id, "provider": PROVIDER},
                tenant_id=tenant.id,
            )
        await self.session.flush()
        return f"{origin}/auth/complete?{urlencode({'hc': handoff})}"

    async def _link_google(
        self, identity: oidc.GoogleIdentity, now: datetime
    ) -> tuple[Customer, bool]:
        """Customer of this Google account: by subject, else by the (verified) e-mail, else new."""
        found = await self._by_subject(identity.subject)
        kept = {k: identity.claims[k] for k in KEPT_CLAIMS if k in identity.claims}
        if found is not None:
            link, customer = found
            link.email_at_provider = identity.email
            link.last_login_at = now
            link.raw_claims = kept
            if customer.full_name is None and identity.name:
                customer.full_name = identity.name
            return customer, False

        existing = (
            await self.session.execute(
                select(Customer).where(Customer.email_normalized == identity.email)
            )
        ).scalar_one_or_none()
        created = existing is None
        try:
            async with self.session.begin_nested():
                if existing is None:
                    customer = Customer(
                        email_normalized=identity.email,
                        email_verified_at=now,
                        full_name=identity.name,
                        status="active",
                    )
                    self.session.add(customer)
                    await self.session.flush()
                else:
                    customer = existing
                    if customer.email_verified_at is None:
                        customer.email_verified_at = now
                self.session.add(
                    CustomerIdentity(
                        customer_id=customer.id,
                        provider=PROVIDER,
                        subject=identity.subject,
                        email_at_provider=identity.email,
                        raw_claims=kept,
                        last_login_at=now,
                    )
                )
                await self.session.flush()
        except IntegrityError:
            # Same Google account signing in twice at once: the other request won the insert.
            again = await self._by_subject(identity.subject)
            if again is None:
                raise
            return again[1], False
        return customer, created

    async def _by_subject(self, subject: str) -> tuple[CustomerIdentity, Customer] | None:
        row = (
            await self.session.execute(
                select(CustomerIdentity, Customer)
                .join(Customer, Customer.id == CustomerIdentity.customer_id)
                .where(CustomerIdentity.provider == PROVIDER)
                .where(CustomerIdentity.subject == subject)
            )
        ).first()
        return (row[0], row[1]) if row else None

    # ------------------------------------------------------------------ 3. complete
    async def complete(
        self,
        tenant: TenantContext,
        *,
        handoff: str,
        binding: str,
        previous_session: str | None,
        ip: str | None,
        user_agent: str | None,
    ) -> Completed:
        now = utcnow()
        handoff_hash = _sha(handoff)
        consumed = await self.session.execute(
            update(CustomerAuthFlow)
            .where(CustomerAuthFlow.handoff_hash == handoff_hash)
            .where(CustomerAuthFlow.handoff_consumed_at.is_(None))
            .where(CustomerAuthFlow.handoff_expires_at > now)
            .where(CustomerAuthFlow.tenant_id == tenant.id)
            .where(CustomerAuthFlow.host == (tenant.host or ""))
            .where(CustomerAuthFlow.binding_hash == _sha(binding))
            .values(handoff_consumed_at=now, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:  # type: ignore[attr-defined]
            raise InvalidHandoffError()
        flow = (
            await self.session.execute(
                select(CustomerAuthFlow).where(CustomerAuthFlow.handoff_hash == handoff_hash)
            )
        ).scalar_one()
        customer = await self.session.get(Customer, flow.customer_id)
        if customer is None:
            raise InvalidHandoffError()

        sessions = CustomerSessionRepository(self.session)
        if previous_session:
            old = await sessions.by_token_hash(hash_token(previous_session))
            if old is not None and old.revoked_at is None:
                old.revoked_at, old.revoked_reason = now, "rotated"
        token, row = open_session(
            self.session,
            tenant_id=tenant.id,
            customer_id=customer.id,
            now=now,
            ip=ip,
            user_agent=user_agent,
        )
        await self.session.flush()
        await audit(
            self.session,
            actor=f"customer:{customer.id}",
            action="customer.signed_in",
            entity_type="customer_session",
            entity_id=row.id,
            tenant_id=tenant.id,
            after={"provider": PROVIDER},
            ip=ip,
            user_agent=user_agent,
        )
        accepted = [
            Acceptance(kind, int(version))
            for kind, version in (("terms", flow.terms_version), ("privacy", flow.privacy_version))
            if version and version.isdigit()
        ]
        if accepted:
            await LegalService(self.session, tenant).record_acceptance(
                customer.id, accepted, at=now, ip=ip, user_agent=user_agent
            )
        access = await AccessRepository(self.session).for_customer(customer.id)
        return Completed(
            session_token=token,
            expires_at=row.expires_at,
            return_to=flow.return_to,
            customer=customer,
            access_status=access.status if access else None,
        )
