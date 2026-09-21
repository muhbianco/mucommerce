from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import CustomerSession, CustomerTenantAccess


class CustomerSessionRepository:
    """Sessions of the session's bound tenant (TenantScoped filter)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_token_hash(self, token_hash: str) -> CustomerSession | None:
        stmt = select(CustomerSession).where(CustomerSession.token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_all(
        self, tenant_id: str, customer_id: str, *, reason: str, now: datetime
    ) -> int:
        result = await self.session.execute(
            update(CustomerSession)
            .where(CustomerSession.tenant_id == tenant_id)
            .where(CustomerSession.customer_id == customer_id)
            .where(CustomerSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


class AccessRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_customer(self, customer_id: str) -> CustomerTenantAccess | None:
        stmt = select(CustomerTenantAccess).where(CustomerTenantAccess.customer_id == customer_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()
