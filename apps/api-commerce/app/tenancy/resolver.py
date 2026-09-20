from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import TtlCache
from app.core.config import settings
from app.core.exceptions import TenantNotFoundError, TenantSuspendedError
from app.core.hosts import InvalidHostnameError, normalize_hostname
from app.tenancy.context import TenantContext, bind_session_tenant
from app.tenancy.models import DomainPurpose, DomainStatus, Tenant, TenantStatus
from app.tenancy.repository import TenantRepository

host_cache = TtlCache("tenant:host")


def _context_from_row(
    tenant: Tenant, host: str | None, features: Any, tenant_settings: Any
) -> TenantContext:
    return TenantContext(
        id=tenant.id,
        slug=tenant.slug,
        public_key=tenant.public_key,
        name=tenant.name,
        status=tenant.status,
        timezone=tenant.timezone,
        locale=tenant.locale,
        currency=tenant.default_currency,
        host=host,
        features=dict(features),
        settings=dict(tenant_settings),
    )


def _context_to_cache(context: TenantContext) -> dict[str, Any]:
    return {
        "id": context.id,
        "slug": context.slug,
        "public_key": context.public_key,
        "name": context.name,
        "status": context.status,
        "timezone": context.timezone,
        "locale": context.locale,
        "currency": context.currency,
        "features": context.features,
        "settings": context.settings,
    }


def _context_from_cache(data: dict[str, Any], host: str) -> TenantContext:
    return TenantContext(host=host, **data)


class TenantResolver:
    """Host header → TenantContext. The only way a request obtains a tenant."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = TenantRepository(session)

    async def resolve_by_host(self, raw_host: str | None) -> TenantContext:
        try:
            host = normalize_hostname(raw_host)
        except InvalidHostnameError as exc:
            raise TenantNotFoundError() from exc

        cached = await host_cache.get(host)
        if cached:
            context = _context_from_cache(cached, host)
        else:
            domain = await self.repo.get_domain_by_hostname(host)
            if (
                domain is None
                or domain.status != DomainStatus.ACTIVE
                or domain.purpose != DomainPurpose.STOREFRONT
            ):
                raise TenantNotFoundError()
            tenant = await self.repo.get(domain.tenant_id)
            if tenant is None or tenant.status in {TenantStatus.DRAFT, TenantStatus.ARCHIVED}:
                raise TenantNotFoundError()
            context = await self.build_context(tenant, host)
            await host_cache.set(
                host, _context_to_cache(context), settings.tenant_cache_ttl_seconds
            )

        if context.status == TenantStatus.SUSPENDED:
            raise TenantSuspendedError()
        bind_session_tenant(self.session, context.id)
        return context

    async def resolve_by_id(self, tenant_id: str) -> TenantContext:
        tenant = await self.repo.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError()
        context = await self.build_context(tenant, None)
        bind_session_tenant(self.session, context.id)
        return context

    async def resolve_by_public_key(self, public_key: str) -> TenantContext:
        tenant = await self.repo.get_by_public_key(public_key)
        if tenant is None:
            raise TenantNotFoundError()
        context = await self.build_context(tenant, None)
        bind_session_tenant(self.session, context.id)
        return context

    async def build_context(self, tenant: Tenant, host: str | None) -> TenantContext:
        features = await self.repo.feature_flags(tenant.id)
        tenant_settings = await self.repo.settings(tenant.id)
        return _context_from_row(tenant, host, features, tenant_settings)


async def invalidate_host_cache(hostnames: list[str]) -> None:
    for hostname in hostnames:
        await host_cache.delete(hostname)
