from __future__ import annotations

from collections.abc import Collection, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import TenantContextMissingError
from app.core.ids import new_id
from app.core.sql import insert_if_missing, is_mariadb
from app.tenancy.context import CROSS_TENANT_OPTION, session_tenant_id
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

    async def list_page(
        self,
        *,
        limit: int,
        before_id: str | None = None,
        status: str | None = None,
        search: str | None = None,
    ) -> Sequence[Tenant]:
        """Newest first, keyset on the primary key (UUIDv7 sorts by creation time).

        `search` matches the store name, the slug or any of its hostnames.

        Returns up to `limit + 1` rows; the extra one only signals a next page.
        """
        stmt = select(Tenant).order_by(Tenant.id.desc()).limit(limit + 1)
        if before_id:
            stmt = stmt.where(Tenant.id < before_id)
        if status:
            stmt = stmt.where(Tenant.status == status)
        term = (search or "").strip()
        if term:
            # The operator typed a name, not a pattern: escape the LIKE wildcards.
            like = "%{}%".format(term.replace("!", "!!").replace("%", "!%").replace("_", "!_"))
            stmt = stmt.where(
                or_(
                    Tenant.name.like(like, escape="!"),
                    Tenant.slug.like(like, escape="!"),
                    select(TenantDomain.id)
                    .where(
                        TenantDomain.tenant_id == Tenant.id,
                        TenantDomain.hostname.like(like, escape="!"),
                    )
                    .exists(),
                )
            )
        return (await self.session.execute(stmt)).scalars().all()

    async def get_many(self, tenant_ids: Collection[str]) -> dict[str, Tenant]:
        if not tenant_ids:
            return {}
        stmt = select(Tenant).where(Tenant.id.in_(list(tenant_ids)))
        return {tenant.id: tenant for tenant in (await self.session.execute(stmt)).scalars()}

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

    async def setting_for_many(
        self, tenant_ids: Collection[str], key: str
    ) -> dict[str, dict[str, object]]:
        """One setting for a page of tenants (ops list), in one query."""
        if not tenant_ids:
            return {}
        stmt = (
            select(TenantSetting)
            .where(TenantSetting.tenant_id.in_(list(tenant_ids)))
            .where(TenantSetting.key == key)
            .execution_options(**{CROSS_TENANT_OPTION: True})
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return {row.tenant_id: dict(row.value) for row in rows}

    async def next_sequence(self, name: str) -> int:
        """Tenant-scoped (session must carry the tenant); concurrent callers get distinct values.

        The row is created with an upsert, so two first calls racing each other do not hit the
        unique key, and then read under a row lock (MariaDB) until the transaction ends.
        """
        tenant_id = session_tenant_id(self.session)
        if tenant_id is None:
            raise TenantContextMissingError()
        mariadb = is_mariadb(self.session)
        values = {"id": new_id(), "tenant_id": tenant_id, "name": name, "next_value": 1}
        await self.session.execute(
            insert_if_missing(
                self.session, TenantSequence.__table__, [values], keep_column="next_value"
            )
        )

        stmt = (
            select(TenantSequence)
            .where(TenantSequence.name == name)
            .execution_options(populate_existing=True)
        )
        if mariadb:
            stmt = stmt.with_for_update()
        seq = (await self.session.execute(stmt)).scalar_one()
        value = seq.next_value
        seq.next_value = value + 1
        await self.session.flush()
        return value
