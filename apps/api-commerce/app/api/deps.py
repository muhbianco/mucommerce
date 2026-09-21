from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import (
    AuthenticationError,
    InactiveUserError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.rate_limit import client_ip
from app.core.scopes import PlatformRole, Scope, platform_role_covers, scopes_for_tenant_role
from app.core.security import constant_time_equals, decode_access_token
from app.identity.models import AdminUser
from app.identity.repository import AdminUserRepository
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/token", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]


# --------------------------------------------------------------------------- admin (JWT)
async def get_current_admin(
    session: DbSession,
    token: Annotated[str | None, Depends(oauth2_scheme)] = None,
) -> AdminUser:
    if not token:
        raise AuthenticationError("Credenciais ausentes.")
    claims = decode_access_token(token)
    user = await AdminUserRepository(session).get(str(claims["sub"]))
    if user is None:
        raise AuthenticationError("Token não corresponde a um usuário existente.")
    if not user.is_active:
        raise InactiveUserError()
    return user


CurrentAdmin = Annotated[AdminUser, Depends(get_current_admin)]

# Marker attributes on guard dependencies, so the leak suite can prove by introspection that
# every /ops and /admin/tenants/{tenant_id} route is behind the right guard.
PLATFORM_GUARD_ATTR = "__platform_guard__"
TENANT_GUARD_ATTR = "__tenant_guard__"


def require_platform_role(required: PlatformRole) -> Callable[..., Awaitable[AdminUser]]:
    async def dependency(user: CurrentAdmin) -> AdminUser:
        if not platform_role_covers(user.platform_role, required):
            raise PermissionDeniedError("Requer papel de plataforma.", required=str(required))
        return user

    setattr(dependency, PLATFORM_GUARD_ATTR, True)
    return dependency


PlatformOperator = Annotated[AdminUser, Depends(require_platform_role(PlatformRole.OPERATOR))]
PlatformSuperadmin = Annotated[AdminUser, Depends(require_platform_role(PlatformRole.SUPERADMIN))]


def require_tenant_scopes(*scopes: Scope) -> Callable[..., Awaitable[TenantContext]]:
    """Tenant comes from the path and is validated against the user's memberships.

    Platform staff pass regardless of membership (support access), but the
    action is still audited with their actor id.
    """

    async def dependency(
        session: DbSession,
        user: CurrentAdmin,
        tenant_id: Annotated[str, Path(min_length=36, max_length=36)],
    ) -> TenantContext:
        if not user.is_platform_admin:
            membership = await AdminUserRepository(session).membership(user.id, tenant_id)
            if membership is None:
                # 404, not 403: never confirm that a tenant id exists to outsiders.
                raise NotFoundError("Tenant não encontrado.")
            allowed = scopes_for_tenant_role(membership.role)
            missing = [s for s in scopes if s not in allowed]
            if missing:
                raise PermissionDeniedError(
                    "Papel no tenant não possui os escopos exigidos.",
                    missing=[str(s) for s in missing],
                )
        return await TenantResolver(session).resolve_by_id(tenant_id)

    setattr(dependency, TENANT_GUARD_ATTR, True)
    return dependency


# --------------------------------------------------------------------------- internal
def require_internal(consumer: str) -> Callable[..., Awaitable[str]]:
    async def dependency(
        x_internal_token: Annotated[str | None, Header()] = None,
    ) -> str:
        expected = settings.internal_token_for(consumer)
        if (
            not expected
            or not x_internal_token
            or not constant_time_equals(x_internal_token, expected)
        ):
            raise AuthenticationError("Token interno inválido.")
        return consumer

    return dependency


async def get_storefront_tenant(
    request: Request,
    session: DbSession,
    x_tenant_host: Annotated[str | None, Header()] = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> TenantContext:
    """Resolve the tenant from `Host`, or from `X-Tenant-Host` when a trusted service calls.

    `X-Tenant-Host` is only honoured together with the web service's internal
    token; a browser sending that header gets the plain `Host` path.
    """
    host = request.headers.get("host")
    if x_tenant_host:
        expected = settings.internal_token_for("web")
        if expected and x_internal_token and constant_time_equals(x_internal_token, expected):
            host = x_tenant_host
    return await TenantResolver(session).resolve_by_host(host)


StorefrontTenant = Annotated[TenantContext, Depends(get_storefront_tenant)]


# --------------------------------------------------------------------------- actor
def admin_actor(request: Request, user: AdminUser) -> Actor:
    return Actor(
        id=f"admin:{user.id}",
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


def bind_tenant(session: AsyncSession, tenant: TenantContext) -> None:
    bind_session_tenant(session, tenant.id)


ClientIp = Annotated[str, Depends(client_ip)]
