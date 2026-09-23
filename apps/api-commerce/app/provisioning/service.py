"""A store bought in the MuhBianco catalog (api-agents), provisioned here.

The money and the store live in different services, so one purchase is three calls:

1. `reserve` — takes the slug and creates the store as `draft`, **before** the wallet is debited.
   A slug already taken answers 409 and nobody is charged.
2. `activate` — after the debit, the store goes `active`.
3. `release` — the debit failed, so the store is archived and its slug freed. The daily sweep
   does the same for a reservation the catalog never came back for (a crash between 1 and 2).

Every call is keyed by the subscription (api-agents `user_services.id`), so a retry adopts the
store the first attempt created instead of making a second one. Nothing here debits, prices or
renews anything: the catalog owns the money, this module owns the store.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.scopes import TenantRole
from app.identity.repository import AdminUserRepository
from app.identity.service import AdminAuthService
from app.models.base import utcnow
from app.tenancy.models import DomainKind, Tenant, TenantStatus
from app.tenancy.resolver import invalidate_host_cache
from app.tenancy.service import Actor, TenantService

# A reservation the catalog never activated is swept after this long (a crash between the slug
# reservation and the debit). Generous on purpose: sweeping a store someone paid for is worse
# than holding a slug for a day.
ORPHAN_RESERVATION_AGE = timedelta(hours=24)

ACTOR = Actor.system("provisioning")


class StoreProvisioningService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tenants = TenantService(session)

    async def by_subscription(self, subscription_ref: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.subscription_ref == subscription_ref)
        return (await self.session.execute(stmt)).scalars().first()

    async def reserve(
        self,
        *,
        subscription_ref: str,
        account_id: str,
        email: str,
        full_name: str,
        slug: str,
        name: str,
    ) -> Tenant:
        """Create the store as `draft` and make the buyer its owner. Idempotent."""
        existing = await self.by_subscription(subscription_ref)
        if existing is not None:
            await self._ensure_owner(existing, account_id, email, full_name)
            return existing

        tenant = await self.tenants.create(slug=slug, name=name, actor=ACTOR, plan="commerce")
        tenant.subscription_ref = subscription_ref
        await self.session.flush()
        await self._ensure_owner(tenant, account_id, email, full_name)
        await emit(
            self.session,
            aggregate_type="tenant",
            aggregate_id=tenant.id,
            event_type="tenant.reserved",
            payload={"slug": tenant.slug, "subscription_ref": subscription_ref},
            tenant_id=tenant.id,
        )
        return tenant

    async def activate(self, subscription_ref: str) -> Tenant:
        """The debit went through: the store goes live. Idempotent."""
        tenant = await self._require(subscription_ref)
        tenant.billing_grace_until = None
        if tenant.status == TenantStatus.ACTIVE:
            return tenant
        if tenant.status not in {TenantStatus.DRAFT, TenantStatus.SUSPENDED}:
            raise ConflictError("Loja não pode ser ativada nesta situação.", status=tenant.status)
        await self.tenants.set_status(tenant, TenantStatus.ACTIVE, ACTOR, reason="subscription")
        if tenant.activated_at is None:
            tenant.activated_at = utcnow()
        await self.session.flush()
        return tenant

    async def release(self, subscription_ref: str) -> Tenant | None:
        """The purchase fell through: archive the store and free the slug for whoever wants it."""
        tenant = await self.by_subscription(subscription_ref)
        if tenant is None:
            return None
        if tenant.status != TenantStatus.DRAFT:
            raise ConflictError("Só uma reserva ainda não ativada pode ser liberada.")
        return await self._archive_reservation(tenant)

    async def set_billing_state(
        self,
        *,
        subscription_ref: str,
        state: str,
        grace_until: datetime | None = None,
        reason: str | None = None,
    ) -> Tenant:
        """`suspended` (with the catalog's grace deadline) or `active` again after a top-up."""
        if state not in {"active", "suspended"}:
            raise ValidationError("Estado de cobrança inválido.", state=state)
        tenant = await self._require(subscription_ref)
        if state == "active":
            return await self.activate(subscription_ref)
        if tenant.status == TenantStatus.DRAFT:
            # Never activated: nothing is being served, so there is nothing to suspend.
            return tenant
        tenant.billing_grace_until = grace_until
        if tenant.status != TenantStatus.SUSPENDED:
            await self.tenants.set_status(
                tenant, TenantStatus.SUSPENDED, ACTOR, reason=reason or "billing"
            )
        await self.session.flush()
        await invalidate_host_cache([d.hostname for d in tenant.domains])
        return tenant

    async def sweep_orphan_reservations(self, *, now: datetime | None = None) -> list[str]:
        """Reservations the catalog never activated (its crash, our held slug)."""
        cutoff = (now or utcnow()) - ORPHAN_RESERVATION_AGE
        stmt = (
            select(Tenant)
            .where(Tenant.status == TenantStatus.DRAFT)
            .where(Tenant.subscription_ref.is_not(None))
            .where(Tenant.created_at < cutoff)
            .limit(100)
        )
        released: list[str] = []
        for tenant in (await self.session.execute(stmt)).scalars().all():
            await self._archive_reservation(tenant)
            released.append(tenant.id)
        return released

    # ------------------------------------------------------------------ internals
    async def _require(self, subscription_ref: str) -> Tenant:
        tenant = await self.by_subscription(subscription_ref)
        if tenant is None:
            raise NotFoundError("Nenhuma loja para esta assinatura.")
        return tenant

    async def _ensure_owner(
        self, tenant: Tenant, account_id: str, email: str, full_name: str
    ) -> None:
        auth = AdminAuthService(self.session)
        owner = await auth.ensure_account_user(
            account_id=account_id, email=email, full_name=full_name
        )
        repo = AdminUserRepository(self.session)
        membership = await repo.any_membership(owner.id, tenant.id)
        if membership is None:
            await auth.add_membership(
                user=owner, tenant_id=tenant.id, role=TenantRole.OWNER, actor=ACTOR.id
            )
        else:
            membership.role = TenantRole.OWNER
            membership.status = "active"
        await self.session.flush()

    async def _archive_reservation(self, tenant: Tenant) -> Tenant:
        """Archive and rename: the slug and the hostname go back to the pool."""
        hosts = [domain.hostname for domain in tenant.domains]
        freed_slug = tenant.slug
        parked = f"{tenant.slug[:40]}-x{tenant.id[-8:]}"
        subscription_ref = tenant.subscription_ref
        for domain in tenant.domains:
            if domain.kind == DomainKind.PLATFORM_SUBDOMAIN:
                domain.hostname = _parked_host(domain.hostname, freed_slug, parked)
        tenant.slug = parked
        tenant.subscription_ref = None
        await self.session.flush()
        await self.tenants.set_status(tenant, TenantStatus.ARCHIVED, ACTOR, reason="released")
        await audit(
            self.session,
            actor=ACTOR.id,
            action="tenant.reservation_released",
            entity_type="tenant",
            entity_id=tenant.id,
            tenant_id=tenant.id,
            before={"slug": freed_slug, "subscription_ref": subscription_ref},
            after={"slug": parked},
        )
        await invalidate_host_cache(hosts)
        return tenant


def _parked_host(hostname: str, slug: str, parked: str) -> str:
    head, _, rest = hostname.partition(".")
    return f"{parked}.{rest}" if head == slug and rest else f"{parked}-{hostname}"
