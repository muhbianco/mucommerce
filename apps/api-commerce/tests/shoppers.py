"""Test helpers for stage E: a store that sells online and signed-in customers of it.

Sessions are opened straight in the database (the Google sign-in itself is covered by
test_customer_login); requests go through the web's path (internal token + X-Tenant-Host +
X-Customer-Session), exactly as the storefront calls the API.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.ids import new_id
from app.customers.sessions import open_session
from app.identity.models import AccessStatus, Customer, CustomerTenantAccess
from app.models.base import utcnow
from app.tenancy.context import bind_session_tenant
from app.tenancy.models import Tenant
from app.tenancy.service import Actor, TenantService
from tests.conftest import create_tenant

WEB_TOKEN = "web-token-test"
SELLING = {
    "storefront": True,
    "catalog": True,
    "inventory": True,
    "checkout": True,
    "customer_login": True,
    "pickup": True,
}


async def selling_store(
    session_factory: async_sessionmaker[AsyncSession],
    slug: str = "alpha",
    *,
    mode: str = "public",
    flags: dict[str, bool] | None = None,
    settings: dict[str, dict[str, Any]] | None = None,
) -> Tenant:
    tenant = await create_tenant(session_factory, slug)
    async with session_factory() as session:
        service = TenantService(session)
        row = await service.get_or_404(tenant.id)
        await service.set_features(row, SELLING | (flags or {}), Actor.system("tests"))
        await service.set_setting(row, "storefront", {"access_mode": mode}, Actor.system("tests"))
        for key, value in (settings or {}).items():
            await service.set_setting(row, key, value, Actor.system("tests"))
        await session.commit()
    return tenant


async def signed_in(
    session_factory: async_sessionmaker[AsyncSession],
    tenant: Tenant,
    *,
    email: str | None = None,
    access: str | None = AccessStatus.APPROVED,
) -> str:
    """A customer with a session in `tenant` (and an access row with `access`); the token."""
    customer_id = new_id()
    async with session_factory() as session:
        session.add(
            Customer(
                id=customer_id,
                email_normalized=email or f"{customer_id[-8:]}@cliente.test",
                email_verified_at=utcnow(),
                full_name="Cliente Teste",
            )
        )
        await session.flush()
        bind_session_tenant(session, tenant.id)
        if access is not None:
            session.add(
                CustomerTenantAccess(
                    tenant_id=tenant.id, customer_id=customer_id, status=access, source="panel"
                )
            )
        token, _ = open_session(
            session,
            tenant_id=tenant.id,
            customer_id=customer_id,
            now=utcnow(),
            ip=None,
            user_agent="tests",
        )
        await session.commit()
    return token


def as_shopper(tenant: Tenant, token: str | None) -> dict[str, str]:
    headers = {"X-Internal-Token": WEB_TOKEN, "X-Tenant-Host": f"{tenant.slug}.loja.test"}
    if token:
        headers["X-Customer-Session"] = token
    return headers
