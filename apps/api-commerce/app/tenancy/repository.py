from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenancy.context import CROSS_TENANT_OPTION
from app.tenancy.models import (
    DomainPurpose,
    DomainStatus,
    Tenant,
    TenantDomain,
    TenantFeatureFlag,
    TenantSequence,
    TenantSetting,
)


class TenantRepository:
    """Queries on the global tenant tables (no tenant context needed)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: str) -> Tenant | None:
        return await self.session.get(Tenant, tenant_id)

    async def get_by_slug(self, slug: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.slug == slug)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_public_key(self, public_key: str) -> Tenant | None:
        stmt = select(Tenant).where(Tenant.public_key == public_key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(self, *, status: str | None = None, limit: int = 100) -> Sequence[Tenant]:
        stmt = select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)
        if status:
            stmt = stmt.where(Tenant.status == status)
        return (await self.session.execute(stmt)).scalars().all()

    async def get_domain_by_hostname(self, hostname: str) -> TenantDomain | None:
        stmt = select(TenantDomain).where(TenantDomain.hostname == hostname)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_domain(self, tenant_id: str, domain_id: str) -> TenantDomain | None:
        stmt = (
            select(TenantDomain)
            .where(TenantDomain.id == domain_id)
            .where(TenantDomain.tenant_id == tenant_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_domains(self, tenant_id: str) -> Sequence[TenantDomain]:
        stmt = (
            select(TenantDomain)
            .where(TenantDomain.tenant_id == tenant_id)
            .order_by(TenantDomain.purpose, TenantDomain.role, TenantDomain.hostname)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_active_storefront_domains(self) -> Sequence[TenantDomain]:
        """All domains that must exist in the edge (Traefik) config, across tenants."""
        stmt = (
            select(TenantDomain)
            .join(Tenant, Tenant.id == TenantDomain.tenant_id)
            .where(TenantDomain.status == DomainStatus.ACTIVE)
            .where(Tenant.status.in_(["active", "suspended", "provisioning"]))
            .order_by(TenantDomain.tenant_id, TenantDomain.purpose, TenantDomain.hostname)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def list_domains_to_verify(self, limit: int = 100) -> Sequence[TenantDomain]:
        stmt = (
            select(TenantDomain)
            .where(
                TenantDomain.status.in_(
                    [DomainStatus.PENDING_DNS, DomainStatus.VERIFYING, DomainStatus.VERIFIED]
                )
            )
            .order_by(TenantDomain.last_check_at.asc().nulls_first())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def primary_domain(
        self, tenant_id: str, purpose: str = DomainPurpose.STOREFRONT
    ) -> TenantDomain | None:
        stmt = (
            select(TenantDomain)
            .where(TenantDomain.tenant_id == tenant_id)
            .where(TenantDomain.purpose == purpose)
            .where(TenantDomain.role == "primary")
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    # --- tenant-scoped tables read while resolving (explicit cross_tenant + filter) ------

    async def feature_flags(self, tenant_id: str) -> dict[str, bool]:
        stmt = (
            select(TenantFeatureFlag)
            .where(TenantFeatureFlag.tenant_id == tenant_id)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.key: row.enabled for row in rows}

    async def settings(self, tenant_id: str) -> dict[str, dict[str, object]]:
        stmt = (
            select(TenantSetting)
            .where(TenantSetting.tenant_id == tenant_id)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.key: dict(row.value) for row in rows}

    async def next_sequence(self, name: str) -> int:
        """Tenant-scoped (session must carry the tenant). Row lock on MariaDB."""
        stmt = select(TenantSequence).where(TenantSequence.name == name)
        if self.session.bind is not None and self.session.bind.dialect.name in {
            "mysql",
            "mariadb",
        }:
            stmt = stmt.with_for_update()
        seq = (await self.session.execute(stmt)).scalar_one_or_none()
        if seq is None:
            seq = TenantSequence(name=name, next_value=1)
            self.session.add(seq)
            await self.session.flush()
        value = seq.next_value
        seq.next_value = value + 1
        await self.session.flush()
        return value
