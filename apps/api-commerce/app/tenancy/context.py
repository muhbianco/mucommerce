from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import tenant_id_var

SESSION_TENANT_KEY = "tenant_id"
CROSS_TENANT_OPTION = "cross_tenant"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Resolved tenant for the current request. Built only by `app.tenancy.resolver`."""

    id: str
    slug: str
    public_key: str
    name: str
    status: str
    timezone: str
    locale: str
    currency: str
    host: str | None = None
    features: dict[str, bool] = field(default_factory=dict)
    settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    def feature(self, key: str) -> bool:
        return bool(self.features.get(key, False))


def bind_session_tenant(session: AsyncSession, tenant_id: str) -> None:
    """Attach the tenant to the session; the ORM filter reads it from `session.info`."""
    session.info[SESSION_TENANT_KEY] = tenant_id
    tenant_id_var.set(tenant_id)


def session_tenant_id(session: AsyncSession) -> str | None:
    value = session.info.get(SESSION_TENANT_KEY)
    return str(value) if value else None
