"""Fila do verify_domains: domínios nunca checados primeiro, depois os mais antigos.

Roda também no MariaDB do CI (TEST_DATABASE_URL): o `NULLS FIRST` que o SQLite aceita é erro de
sintaxe no MariaDB e derrubava a task a cada 5 minutos em produção.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.tenancy.models import DomainPurpose, DomainRole, DomainStatus, TenantDomain
from app.tenancy.repository import TenantRepository
from tests.conftest import create_tenant


def _domain(
    tenant_id: str, hostname: str, status: str, last_check_at: datetime | None
) -> TenantDomain:
    return TenantDomain(
        tenant_id=tenant_id,
        hostname=hostname,
        kind="custom_apex",
        purpose=DomainPurpose.STOREFRONT,
        role=DomainRole.ALIAS,
        status=status,
        last_check_at=last_check_at,
    )


async def test_never_checked_domains_come_first_then_oldest(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "fila-dns")
    now = datetime.now(UTC).replace(microsecond=0)
    ours = {
        "antigo.example": now - timedelta(hours=2),
        "nunca.example": None,
        "recente.example": now - timedelta(minutes=5),
    }
    async with session_factory() as session:
        session.add_all(
            [
                _domain(tenant.id, host, DomainStatus.VERIFYING, checked)
                for host, checked in ours.items()
            ]
            + [_domain(tenant.id, "ativo.example", DomainStatus.ACTIVE, None)]
        )
        await session.commit()

    async with session_factory() as session:
        queue = await TenantRepository(session).list_domains_to_verify(limit=100)

    hostnames = [d.hostname for d in queue if d.hostname.endswith(".example")]
    assert hostnames == ["nunca.example", "antigo.example", "recente.example"]
