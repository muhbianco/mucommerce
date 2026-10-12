"""Access of a customer to one store (the whitelist): requests by the customer, decisions by
the store. `customer_tenant_access` is the source of truth; Chatwoot mirrors it in phase B
through the `customer.access.*` events."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.exceptions import (
    AccessBlockedError,
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
    ValidationError,
)
from app.customers.models import CustomerPhone
from app.customers.repository import AccessRepository, CustomerSessionRepository
from app.identity.models import AccessStatus, CustomerSession, CustomerTenantAccess
from app.models.base import utcnow
from app.orders.models import Order
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

# What the store can decide from each state. Approving again, or blocking twice, is a no-op.
TRANSITIONS: dict[str, frozenset[str]] = {
    AccessStatus.PENDING: frozenset({AccessStatus.APPROVED, AccessStatus.BLOCKED}),
    AccessStatus.APPROVED: frozenset({AccessStatus.REVOKED, AccessStatus.BLOCKED}),
    AccessStatus.REVOKED: frozenset({AccessStatus.APPROVED, AccessStatus.BLOCKED}),
    AccessStatus.BLOCKED: frozenset({AccessStatus.APPROVED, AccessStatus.REVOKED}),
}
NOTE_REQUIRED = frozenset({AccessStatus.BLOCKED, AccessStatus.REVOKED})


class CustomerAccessService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AccessRepository(session)

    async def current(self, customer_id: str) -> CustomerTenantAccess | None:
        return await self.repo.for_customer(customer_id)

    async def request(
        self, tenant: TenantContext, customer_id: str, *, message: str | None, ip: str | None
    ) -> CustomerTenantAccess:
        """Ask the store for access. Asking again while pending only updates the message."""
        row = await self.repo.for_customer(customer_id)
        if row is not None and row.status == AccessStatus.APPROVED:
            raise ConflictError("Seu acesso já está liberado.", code="already_approved")
        if row is not None and row.status == AccessStatus.BLOCKED:
            raise AccessBlockedError()
        if row is not None and row.status == AccessStatus.PENDING:
            if message:
                row.request_message = message
            return row

        now = utcnow()
        actor = f"customer:{customer_id}"
        before = {"status": row.status} if row is not None else None
        if row is None:
            row = CustomerTenantAccess(customer_id=customer_id)
            self.session.add(row)
        row.status = AccessStatus.PENDING
        row.source = "request"
        row.requested_at = now
        row.request_message = message
        row.status_changed_at = now
        row.status_changed_by_actor = actor
        await self.session.flush()
        await audit(
            self.session,
            actor=actor,
            action="customer.access.requested",
            entity_type="customer_tenant_access",
            entity_id=row.id,
            tenant_id=tenant.id,
            before=before,
            after={"status": str(AccessStatus.PENDING)},
            ip=ip,
        )
        await emit(
            self.session,
            aggregate_type="customer_access",
            aggregate_id=row.id,
            event_type="customer.access.requested",
            payload={"customer_id": customer_id, "status": str(AccessStatus.PENDING)},
            tenant_id=tenant.id,
        )
        return row

    async def _known_here(self, customer_id: str) -> bool:
        """A pessoa já passou por esta loja: pedido, sessão ou telefone registrado aqui."""
        for stmt in (
            select(Order.id).where(Order.customer_id == customer_id).limit(1),
            select(CustomerSession.id).where(CustomerSession.customer_id == customer_id).limit(1),
            select(CustomerPhone.id).where(CustomerPhone.customer_id == customer_id).limit(1),
        ):
            if await self.session.scalar(stmt) is not None:
                return True
        return False

    async def decide(
        self,
        tenant: TenantContext,
        customer_id: str,
        *,
        status: str,
        note: str | None,
        actor: Actor,
    ) -> CustomerTenantAccess:
        """The store approves, blocks or revokes a customer who signed in to it."""
        row = await self.repo.for_customer(customer_id)
        if row is None:
            # Quem comprou pela senha da loja (ou só deixou o WhatsApp no checkout) não tem
            # linha de acesso: liberar cria a linha — mas só para quem já passou por esta loja.
            if status != AccessStatus.APPROVED or not await self._known_here(customer_id):
                raise NotFoundError("Cliente não encontrado nesta loja.")
            row = CustomerTenantAccess(
                tenant_id=tenant.id,
                customer_id=customer_id,
                status=AccessStatus.PENDING,
                source="panel",
                requested_at=utcnow(),
            )
            self.session.add(row)
            await self.session.flush()
        if status not in TRANSITIONS:
            raise ValidationError("Status inválido.", fields=["status"])
        if status in NOTE_REQUIRED and not (note and note.strip()):
            raise ValidationError("Informe o motivo.", fields=["note"])
        if row.status == status:
            return row
        if status not in TRANSITIONS.get(row.status, frozenset()):
            raise InvalidTransitionError(**{"from": row.status, "to": status})

        now = utcnow()
        before = row.status
        row.status = status
        row.note = note.strip() if note else row.note
        row.status_changed_at = now
        row.status_changed_by_actor = actor.id
        row.source = "panel" if row.source != "request" else row.source
        if status == AccessStatus.APPROVED:
            row.approved_at, row.approved_by_actor = now, actor.id
        if status == AccessStatus.BLOCKED:
            # Signed out everywhere in this store at once, not at the next page view.
            await CustomerSessionRepository(self.session).revoke_all(
                tenant.id, customer_id, reason="blocked", now=now
            )
        await self.session.flush()
        await audit(
            self.session,
            actor=actor.id,
            action=f"customer.access.{status}",
            entity_type="customer_tenant_access",
            entity_id=row.id,
            tenant_id=tenant.id,
            before={"status": before},
            after={"status": status, "note": row.note},
            ip=actor.ip,
            user_agent=actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="customer_access",
            aggregate_id=row.id,
            event_type=f"customer.access.{status}",
            payload={"customer_id": customer_id, "status": status, "from": before},
            tenant_id=tenant.id,
        )
        return row
