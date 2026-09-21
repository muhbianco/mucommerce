from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.exceptions import ConflictError
from app.models.base import utcnow
from app.tenancy.dns import DnsCheck
from app.tenancy.models import DomainPurpose, DomainRole, DomainStatus, TenantDomain
from app.tenancy.service import TenantService
from tests.conftest import create_tenant


class _Verifier:
    def __init__(self, check: DnsCheck) -> None:
        self.check_result = check

    async def check(self, hostname: str, token: str) -> DnsCheck:
        del hostname, token
        return self.check_result


def _domain(tenant_id: str, status: str, **extra: object) -> TenantDomain:
    return TenantDomain(
        tenant_id=tenant_id,
        hostname="loja.cliente.com.br",
        kind="custom_subdomain",
        purpose=DomainPurpose.STOREFRONT,
        role=DomainRole.ALIAS,
        status=status,
        **extra,
    )


async def test_verified_domain_activates_even_after_the_txt_was_changed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Regression: TXT seen once, then edited by the customer while adding the CNAME. The
    domain stayed `verified` forever because activation asked for the TXT again."""
    tenant = await create_tenant(session_factory, "cliente")
    async with session_factory() as session:
        domain = _domain(tenant.id, DomainStatus.VERIFIED, verified_at=utcnow())
        session.add(domain)
        await session.flush()
        check = DnsCheck(
            txt_ok=False,
            target_ok=True,
            observed_txt=["mb-verify=mb-verify=tok"],
            observed_cname="edge.test",
        )
        await TenantService(session).verify_domain(domain, _Verifier(check))  # type: ignore[arg-type]
        assert domain.status == DomainStatus.ACTIVE
        assert domain.last_error is None


async def test_verified_domain_waits_for_the_target_and_never_fails_by_age(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "cliente")
    async with session_factory() as session:
        old = utcnow() - timedelta(hours=settings.domain_verify_max_age_hours + 1)
        domain = _domain(tenant.id, DomainStatus.VERIFIED, verified_at=old, created_at=old)
        session.add(domain)
        await session.flush()
        await TenantService(session).verify_domain(domain, _Verifier(DnsCheck()))  # type: ignore[arg-type]
        assert domain.status == DomainStatus.VERIFIED
        assert domain.last_error == "TXT ok; A/CNAME ainda não aponta para a plataforma"


@pytest.mark.parametrize(
    ("observed_txt", "message"),
    [
        (
            ["mb-verify=mb-verify=tok"],
            "TXT encontrado, mas o valor não confere com o indicado no painel",
        ),
        ([], "TXT de verificação não encontrado"),
    ],
)
async def test_unverified_domain_tells_a_wrong_txt_from_a_missing_one(
    session_factory: async_sessionmaker[AsyncSession], observed_txt: list[str], message: str
) -> None:
    tenant = await create_tenant(session_factory, "cliente")
    async with session_factory() as session:
        domain = _domain(tenant.id, DomainStatus.PENDING_DNS)
        session.add(domain)
        await session.flush()
        check = DnsCheck(target_ok=True, observed_txt=observed_txt)
        await TenantService(session).verify_domain(domain, _Verifier(check))  # type: ignore[arg-type]
        assert domain.status == DomainStatus.VERIFYING
        assert domain.verified_at is None
        assert domain.last_error == message


async def test_disabled_domain_is_not_verified(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tenant = await create_tenant(session_factory, "cliente")
    async with session_factory() as session:
        domain = _domain(tenant.id, DomainStatus.DISABLED, verified_at=utcnow())
        session.add(domain)
        await session.flush()
        check = DnsCheck(txt_ok=True, target_ok=True)
        with pytest.raises(ConflictError):
            await TenantService(session).verify_domain(domain, _Verifier(check))  # type: ignore[arg-type]
        assert domain.status == DomainStatus.DISABLED
