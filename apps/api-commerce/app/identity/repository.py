from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select
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

    async def purge_expired_refresh(
        self, *, older_than: timedelta, batch_size: int = 500, max_batches: int = 20
    ) -> int:
        """Delete refresh tokens expired for longer than `older_than`, in bounded batches.

        Rotated/revoked tokens are kept until they expire so reuse detection still sees them.
        """
        cutoff = utcnow() - older_than
        deleted = 0
        for _ in range(max_batches):
            ids = list(
                (
                    await self.session.execute(
                        select(AdminRefreshToken.id)
                        .where(AdminRefreshToken.expires_at < cutoff)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not ids:
                break
            await self.session.execute(
                delete(AdminRefreshToken)
                .where(AdminRefreshToken.id.in_(ids))
                .execution_options(synchronize_session=False)
            )
            deleted += len(ids)
            if len(ids) < batch_size:
                break
        return deleted
