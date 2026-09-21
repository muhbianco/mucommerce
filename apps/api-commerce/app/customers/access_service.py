"""Access of a customer to one store (the whitelist): requests by the customer, decisions by
the store. `customer_tenant_access` is the source of truth; Chatwoot mirrors it in phase B
through the `customer.access.*` events."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.exceptions import AccessBlockedError, ConflictError
from app.customers.repository import AccessRepository
from app.identity.models import AccessStatus, CustomerTenantAccess
from app.models.base import utcnow
from app.tenancy.context import TenantContext


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
