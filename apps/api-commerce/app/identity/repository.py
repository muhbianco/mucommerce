from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import AdminRefreshToken, AdminUser, TenantMembership
from app.models.base import utcnow


class AdminUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, user_id: str) -> AdminUser | None:
        return await self.session.get(AdminUser, user_id)

    async def get_by_email(self, email: str) -> AdminUser | None:
        stmt = select(AdminUser).where(AdminUser.email == email.strip().lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def membership(self, user_id: str, tenant_id: str) -> TenantMembership | None:
        stmt = (
            select(TenantMembership)
            .where(TenantMembership.admin_user_id == user_id)
            .where(TenantMembership.tenant_id == tenant_id)
            .where(TenantMembership.status == "active")
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_refresh_by_hash(self, token_hash: str) -> AdminRefreshToken | None:
        stmt = select(AdminRefreshToken).where(AdminRefreshToken.token_hash == token_hash)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def revoke_all_refresh(self, user_id: str) -> int:
        stmt = (
            select(AdminRefreshToken)
            .where(AdminRefreshToken.admin_user_id == user_id)
            .where(AdminRefreshToken.revoked_at.is_(None))
        )
        tokens = (await self.session.execute(stmt)).scalars().all()
        now = utcnow()
        for token in tokens:
            token.revoked_at = now
        return len(tokens)
