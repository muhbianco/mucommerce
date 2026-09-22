from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import (
    AccessBlockedError,
    AuthenticationError,
    CsrfOriginError,
    FeatureDisabledError,
    InactiveUserError,
    LoginRequiredError,
    NotFoundError,
    PayloadTooLargeError,
    PermissionDeniedError,
)
from app.core.hosts import InvalidHostnameError, normalize_hostname
from app.core.rate_limit import client_ip
from app.core.scopes import PlatformRole, Scope, platform_role_covers, scopes_for_tenant_role
from app.core.security import constant_time_equals, decode_access_token
from app.customers.access import Viewer, check_catalog_access
from app.customers.sessions import SESSION_COOKIE, resolve_viewer
from app.identity.models import AccessStatus, AdminUser
from app.identity.repository import AdminUserRepository
from app.models.base import utcnow
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
CATALOG_ACCESS_GUARD_ATTR = "__catalog_access_guard__"
CHECKOUT_GUARD_ATTR = "__checkout_guard__"
CUSTOMER_GUARD_ATTR = "__customer_guard__"
WEBHOOK_GUARD_ATTR = "__webhook_guard__"


def require_platform_role(required: PlatformRole) -> Callable[..., Awaitable[AdminUser]]:
    async def dependency(user: CurrentAdmin) -> AdminUser:
        if not platform_role_covers(user.platform_role, required):
            raise PermissionDeniedError("Requer papel de plataforma.", required=str(required))
        return user

    setattr(dependency, PLATFORM_GUARD_ATTR, True)
    return dependency


PlatformOperator = Annotated[AdminUser, Depends(require_platform_role(PlatformRole.OPERATOR))]
PlatformSuperadmin = Annotated[AdminUser, Depends(require_platform_role(PlatformRole.SUPERADMIN))]


def require_tenant_scopes(
    *scopes: Scope, features: tuple[str, ...] = (), members_only: bool = False
) -> Callable[..., Awaitable[TenantContext]]:
    """Tenant comes from the path and is validated against the user's memberships.

    Platform staff pass regardless of membership (support access), but the
    action is still audited with their actor id. `features` must all be enabled for the
    tenant (panel routes of a module that is switched off answer 403 `feature_disabled`).
    `members_only`: platform staff get no bypass — only the store's own members, by their
    role (payment credentials: who sets the token decides where the money goes).
    """

    async def dependency(
        session: DbSession,
        user: CurrentAdmin,
        tenant_id: Annotated[str, Path(min_length=36, max_length=36)],
    ) -> TenantContext:
        if members_only or not user.is_platform_admin:
            membership = await AdminUserRepository(session).membership(user.id, tenant_id)
            if membership is None and user.is_platform_admin:
                raise PermissionDeniedError("Só a equipe da loja pode fazer esta operação.")
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
        tenant = await TenantResolver(session).resolve_by_id(tenant_id)
        disabled = [f for f in features if not tenant.feature(f)]
        if disabled:
            raise FeatureDisabledError(features=disabled)
        return tenant

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


def _is_web(x_internal_token: str | None) -> bool:
    expected = settings.internal_token_for("web")
    return bool(expected and x_internal_token and constant_time_equals(x_internal_token, expected))


# --------------------------------------------------------------------------- customer session
async def get_optional_customer(
    request: Request,
    session: DbSession,
    tenant: StorefrontTenant,
    x_customer_session: Annotated[str | None, Header()] = None,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> Viewer | None:
    """The signed-in customer of THIS store, if any.

    The browser sends the host-only `__Host-mb_sess` cookie on `/api` of the store's host; the
    web's server-side calls forward the same token in `X-Customer-Session`, honoured only with
    the web's internal token. The session must belong to the resolved store.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if x_customer_session and _is_web(x_internal_token):
        token = x_customer_session
    if not token:
        return None
    return await resolve_viewer(session, tenant.id, token, utcnow())


OptionalCustomer = Annotated[Viewer | None, Depends(get_optional_customer)]


async def require_customer(viewer: OptionalCustomer) -> Viewer:
    if viewer is None:
        raise LoginRequiredError()
    return viewer


setattr(require_customer, CUSTOMER_GUARD_ATTR, True)
CurrentCustomer = Annotated[Viewer, Depends(require_customer)]


async def require_same_origin(
    request: Request,
    tenant: StorefrontTenant,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """CSRF guard for cookie-authenticated POSTs from the browser.

    SameSite=Lax does not stop requests from sibling subdomains of the platform domain, so the
    request must also come from the store's own origin (Origin header, or the browser's
    `Sec-Fetch-Site: same-origin`). The store's web calls with its token and checks on its side.
    """
    if _is_web(x_internal_token):
        return
    origin = request.headers.get("origin")
    if origin and tenant.host and origin == settings.storefront_origin(tenant.host):
        return
    if request.headers.get("sec-fetch-site") == "same-origin":
        return
    raise CsrfOriginError()


def storefront_access_mode(tenant: TenantContext) -> str:
    return str(tenant.settings.get("storefront", {}).get("access_mode", "whitelist"))


def check_storefront_catalog(tenant: TenantContext, viewer: Viewer | None) -> None:
    """Raise unless the catalog of `tenant` is visible to `viewer` (see check_catalog_access)."""
    if not (tenant.feature("storefront") and tenant.feature("catalog")):
        raise NotFoundError("Recurso não encontrado.")
    check_catalog_access(storefront_access_mode(tenant), viewer)


async def require_catalog_access(
    tenant: StorefrontTenant, viewer: OptionalCustomer
) -> TenantContext:
    """Gate for every storefront catalog read.

    - `storefront` or `catalog` off → 404, as if the store had no catalog;
    - `public` → open; otherwise a session of this store is needed (401 `login_required`),
      and `whitelist` also needs approval (403 `access_pending` / `access_required`);
      blocked customers get 403 `access_blocked`.

    The web's internal token only changes which Host is resolved and lets the web forward the
    customer's session; it never grants access by itself.
    """
    check_storefront_catalog(tenant, viewer)
    return tenant


setattr(require_catalog_access, CATALOG_ACCESS_GUARD_ATTR, True)
CatalogReader = Annotated[TenantContext, Depends(require_catalog_access)]


async def require_events_access(tenant: CatalogReader) -> TenantContext:
    """Catalog access (above), plus the store's `events` module: off → 404, as if it had none."""
    if not tenant.feature("events"):
        raise NotFoundError("Recurso não encontrado.")
    return tenant


EventsReader = Annotated[TenantContext, Depends(require_events_access)]


# --------------------------------------------------------------------------- checkout
@dataclass(frozen=True, slots=True)
class Shopper:
    tenant: TenantContext
    viewer: Viewer


async def require_checkout(tenant: StorefrontTenant, viewer: CurrentCustomer) -> Shopper:
    """Gate for cart, checkout and the customer's addresses (ADR 0011).

    - `storefront`, `catalog` or `checkout` off → 404, as if the store did not sell online;
    - a session of this store is required in every access mode (401 `login_required`);
    - the catalog access rules apply (whitelist needs approval), and a blocked customer never
      buys, not even in a public store (403 `access_blocked`).
    """
    if not (tenant.feature("storefront") and tenant.feature("catalog")):
        raise NotFoundError("Recurso não encontrado.")
    if not tenant.feature("checkout"):
        raise NotFoundError("Recurso não encontrado.")
    check_catalog_access(storefront_access_mode(tenant), viewer)
    if viewer.access_status == AccessStatus.BLOCKED:
        raise AccessBlockedError()
    return Shopper(tenant, viewer)


setattr(require_checkout, CHECKOUT_GUARD_ATTR, True)
CheckoutShopper = Annotated[Shopper, Depends(require_checkout)]


def customer_rate_key(request: Request) -> str:
    """Rate-limit key of the customer session (forwarded by the web, or the browser cookie);
    hashed, so the token never reaches Redis. Falls back to the client IP."""
    token = request.headers.get("x-customer-session") or request.cookies.get(SESSION_COOKIE)
    if not token:
        return client_ip(request)
    return hashlib.sha256(token.encode()).hexdigest()[:32]


# --------------------------------------------------------------------------- webhooks
async def require_webhook_store(
    request: Request,
    session: DbSession,
    tenant_key: Annotated[str, Path(min_length=16, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")],
) -> TenantContext:
    """Gate for payment webhooks: only on the public API host, and the store comes only from the
    key in the URL (never from the body). Suspended stores still take webhooks — money may be
    moving — and so do stores whose `checkout` flag was switched off meanwhile."""
    try:
        host = normalize_hostname(request.headers.get("host"))
    except InvalidHostnameError as exc:
        raise NotFoundError("Recurso não encontrado.") from exc
    if host != settings.api_public_host:
        raise NotFoundError("Recurso não encontrado.")
    return await TenantResolver(session).resolve_by_public_key(tenant_key)


setattr(require_webhook_store, WEBHOOK_GUARD_ATTR, True)
WebhookStore = Annotated[TenantContext, Depends(require_webhook_store)]


async def read_body_limited(request: Request, limit: int) -> bytes:
    """The raw body, refusing more than `limit` bytes without buffering them (413)."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise PayloadTooLargeError(limit=limit)
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLargeError(limit=limit)
        chunks.append(chunk)
    return b"".join(chunks)


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
