from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.writer import audit
from app.core.config import settings
from app.core.exceptions import AuthenticationError, ConflictError, InactiveUserError
from app.core.logging import get_logger
from app.core.scopes import PlatformRole, scopes_for_tenant_role
from app.core.security import (
    check_password,
    create_access_token,
    encode_access_token,
    generate_opaque_token,
    hash_password,
    hash_token,
)
from app.identity.accounts import Account
from app.identity.models import AdminRefreshToken, AdminUser, TenantMembership
from app.identity.repository import AdminUserRepository
from app.models.base import utcnow

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AdminAuthService:
    """Staff sign-in: MuhBianco account (the normal path) or a local password (break-glass)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = AdminUserRepository(session)

    async def login(
        self, email: str, password: str, *, ip: str | None, user_agent: str | None
    ) -> TokenPair:
        user = await self.repo.get_by_email(email)
        # Same error and same Argon2 cost for unknown e-mail and wrong password.
        password_ok = await check_password(password, user.password_hash if user else None)
        if user is None or not password_ok:
            raise AuthenticationError()
        if not user.is_active:
            raise InactiveUserError()
        user.last_login_at = utcnow()
        await audit(
            self.session,
            actor=f"admin:{user.id}",
            action="admin.login",
            entity_type="admin_user",
            entity_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        return await self._issue(user, ip=ip, user_agent=user_agent)

    async def login_with_account(
        self,
        account: Account,
        *,
        ip: str | None,
        user_agent: str | None,
        issue_refresh: bool = True,
    ) -> TokenPair:
        """Sign in with a MuhBianco account. The panel user is found by account id; the
        platform role follows the account's site role on every sign-in (site admin → platform
        superadmin, anything else → none), so the site stays the one place that says who
        runs MuhBianco. Tenant memberships are the panel's own."""
        user = await self._link_account(account)
        if not user.is_active:
            raise InactiveUserError()
        platform_role = str(PlatformRole.SUPERADMIN) if account.is_platform_admin else None
        before = user.platform_role
        user.platform_role = platform_role
        user.last_login_at = utcnow()
        await self.session.flush()
        await audit(
            self.session,
            actor=f"admin:{user.id}",
            action="admin.login",
            entity_type="admin_user",
            entity_id=user.id,
            before={"platform_role": before} if before != platform_role else None,
            after={"method": "muhbianco_account", "platform_role": platform_role},
            ip=ip,
            user_agent=user_agent,
        )
        if issue_refresh:
            return await self._issue(user, ip=ip, user_agent=user_agent)
        access = create_access_token(user.id, user.platform_role)
        return TokenPair(
            access_token=encode_access_token(access),
            refresh_token="",
            expires_in=settings.access_token_ttl_minutes * 60,
        )

    async def _link_account(self, account: Account) -> AdminUser:
        user = await self.repo.get_by_external_account(account.account_id)
        if user is None:
            by_email = await self.repo.get_by_email(account.email)
            if by_email is not None:
                # An account may take over an existing e-mail only when that e-mail is verified
                # and the panel user is not already tied to another account.
                if by_email.external_account_id is not None or not account.email_verified:
                    raise AuthenticationError("E-mail já vinculado a outra conta.")
                by_email.external_account_id = account.account_id
                user = by_email
            else:
                user = AdminUser(
                    email=account.email,
                    full_name=account.full_name,
                    external_account_id=account.account_id,
                )
                self.session.add(user)
        elif user.email != account.email:
            clash = await self.repo.get_by_email(account.email)
            if clash is None or clash.id == user.id:
                user.email = account.email
        if account.full_name and user.full_name != account.full_name:
            user.full_name = account.full_name
        await self.session.flush()
        return user

    async def ensure_account_user(
        self, *, account_id: str, email: str, full_name: str
    ) -> AdminUser:
        """Panel user for a MuhBianco account that may not have signed in yet (ops assigns a
        store owner by account). Linked by id; the e-mail is refreshed at their first sign-in."""
        user = await self.repo.get_by_external_account(account_id)
        if user is not None:
            return user
        email = email.strip().lower()
        by_email = await self.repo.get_by_email(email)
        if by_email is not None:
            if by_email.external_account_id not in (None, account_id):
                raise ConflictError("E-mail já vinculado a outra conta MuhBianco.")
            by_email.external_account_id = account_id
            await self.session.flush()
            return by_email
        user = AdminUser(
            email=email, full_name=full_name.strip() or email, external_account_id=account_id
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def refresh(
        self, raw_refresh: str, *, ip: str | None, user_agent: str | None
    ) -> TokenPair:
        stored = await self.repo.get_refresh_by_hash(hash_token(raw_refresh))
        if stored is None:
            raise AuthenticationError("Refresh token inválido.")
        if stored.revoked_at is not None:
            # Reuse of a rotated token: someone replayed it. Kill the whole family.
            revoked = await self.repo.revoke_all_refresh(stored.admin_user_id)
            logger.warning(
                "Refresh token reuse detected; sessions revoked",
                extra={"admin_user_id": stored.admin_user_id, "revoked": revoked},
            )
            await audit(
                self.session,
                actor=f"admin:{stored.admin_user_id}",
                action="admin.refresh_reuse_detected",
                entity_type="admin_user",
                entity_id=stored.admin_user_id,
                ip=ip,
                user_agent=user_agent,
            )
            # The request ends in 401 (rollback path), so persist the revocation here.
            await self.session.commit()
            raise AuthenticationError("Sessão revogada por reuso de token.")
        if stored.expires_at < utcnow():
            raise AuthenticationError("Refresh token expirado.")
        user = await self.repo.get(stored.admin_user_id)
        if user is None or not user.is_active:
            raise InactiveUserError()

        stored.revoked_at = utcnow()
        pair = await self._issue(user, ip=ip, user_agent=user_agent)
        # Link old → new for forensics.
        new_hash = hash_token(pair.refresh_token)
        new_row = await self.repo.get_refresh_by_hash(new_hash)
        if new_row is not None:
            stored.replaced_by_id = new_row.id
        return pair

    async def logout(self, raw_refresh: str | None, user: AdminUser | None) -> None:
        if raw_refresh:
            stored = await self.repo.get_refresh_by_hash(hash_token(raw_refresh))
            if stored is not None and stored.revoked_at is None:
                stored.revoked_at = utcnow()
        if user is not None:
            await audit(
                self.session,
                actor=f"admin:{user.id}",
                action="admin.logout",
                entity_type="admin_user",
                entity_id=user.id,
            )

    async def _issue(self, user: AdminUser, *, ip: str | None, user_agent: str | None) -> TokenPair:
        access = create_access_token(user.id, user.platform_role)
        raw_refresh, refresh_hash = generate_opaque_token()
        self.session.add(
            AdminRefreshToken(
                admin_user_id=user.id,
                token_hash=refresh_hash,
                expires_at=utcnow() + timedelta(days=settings.refresh_token_ttl_days),
                ip=ip,
                user_agent=user_agent[:300] if user_agent else None,
            )
        )
        await self.session.flush()
        return TokenPair(
            access_token=encode_access_token(access),
            refresh_token=raw_refresh,
            expires_in=settings.access_token_ttl_minutes * 60,
        )

    async def create_user(
        self,
        *,
        email: str,
        full_name: str,
        password: str | None,
        platform_role: str | None,
        actor: str,
    ) -> AdminUser:
        user = AdminUser(
            email=email.strip().lower(),
            full_name=full_name.strip(),
            password_hash=hash_password(password) if password else None,
            platform_role=platform_role,
        )
        self.session.add(user)
        await self.session.flush()
        await audit(
            self.session,
            actor=actor,
            action="admin.created",
            entity_type="admin_user",
            entity_id=user.id,
            after={"email": user.email, "platform_role": platform_role},
        )
        return user

    async def add_membership(
        self, *, user: AdminUser, tenant_id: str, role: str, actor: str
    ) -> TenantMembership:
        scopes_for_tenant_role(role)  # validates the role
        membership = TenantMembership(tenant_id=tenant_id, admin_user_id=user.id, role=role)
        self.session.add(membership)
        await self.session.flush()
        await audit(
            self.session,
            actor=actor,
            action="membership.created",
            entity_type="tenant_membership",
            entity_id=membership.id,
            tenant_id=tenant_id,
            after={"admin_user_id": user.id, "role": role},
        )
        return membership
