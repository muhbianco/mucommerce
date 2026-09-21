from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.customers.models import CustomerAuthFlow
from app.identity.models import Customer, CustomerSession, CustomerTenantAccess
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION


class CustomerSessionRepository:
    """Sessions of the session's bound tenant (TenantScoped filter)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def by_id(self, session_id: str) -> CustomerSession | None:
        stmt = select(CustomerSession).where(CustomerSession.id == session_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

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
    """Access rows of the bound store (TenantScoped), joined with the global customer."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_customer(self, customer_id: str) -> CustomerTenantAccess | None:
        stmt = select(CustomerTenantAccess).where(CustomerTenantAccess.customer_id == customer_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def with_customer(self, customer_id: str) -> tuple[CustomerTenantAccess, Customer] | None:
        row = (
            await self.session.execute(
                select(CustomerTenantAccess, Customer)
                .join(Customer, Customer.id == CustomerTenantAccess.customer_id)
                .where(CustomerTenantAccess.customer_id == customer_id)
            )
        ).first()
        return (row[0], row[1]) if row else None

    async def list_page(
        self, *, limit: int, before_id: str | None, status: str | None, q: str | None
    ) -> list[tuple[CustomerTenantAccess, Customer]]:
        """Newest first (UUIDv7 ids), `limit + 1` rows so the caller knows if there is more."""
        stmt = (
            select(CustomerTenantAccess, Customer)
            .join(Customer, Customer.id == CustomerTenantAccess.customer_id)
            .order_by(CustomerTenantAccess.id.desc())
            .limit(limit + 1)
        )
        if before_id:
            stmt = stmt.where(CustomerTenantAccess.id < before_id)
        if status:
            stmt = stmt.where(CustomerTenantAccess.status == status)
        if q:
            stmt = stmt.where(
                or_(
                    Customer.email_normalized.contains(q.lower(), autoescape=True),
                    Customer.full_name.contains(q, autoescape=True),
                    Customer.phone_e164.startswith(q, autoescape=True),
                )
            )
        return [(row[0], row[1]) for row in (await self.session.execute(stmt)).all()]


async def purge_auth_flows(
    session: AsyncSession, *, older_than: timedelta, batch_size: int = 500, max_batches: int = 20
) -> int:
    """Delete sign-in flows created more than `older_than` ago (all are long dead by then)."""
    cutoff = utcnow() - older_than
    deleted = 0
    for _ in range(max_batches):
        ids = list(
            (
                await session.execute(
                    select(CustomerAuthFlow.id)
                    .where(CustomerAuthFlow.created_at < cutoff)
                    .limit(batch_size)
                )
            ).scalars()
        )
        if not ids:
            break
        await session.execute(delete(CustomerAuthFlow).where(CustomerAuthFlow.id.in_(ids)))
        deleted += len(ids)
    return deleted


async def purge_sessions(
    session: AsyncSession, *, older_than: timedelta, batch_size: int = 500, max_batches: int = 20
) -> int:
    """Delete sessions (every store) expired or revoked more than `older_than` ago."""
    cutoff = utcnow() - older_than
    deleted = 0
    for _ in range(max_batches):
        ids = list(
            (
                await session.execute(
                    select(CustomerSession.id)
                    .where(
                        or_(
                            CustomerSession.expires_at < cutoff, CustomerSession.revoked_at < cutoff
                        )
                    )
                    .limit(batch_size)
                    .execution_options(**{CROSS_TENANT_OPTION: True})
                )
            ).scalars()
        )
        if not ids:
            break
        await session.execute(
            delete(CustomerSession)
            .where(CustomerSession.id.in_(ids))
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        deleted += len(ids)
    return deleted
