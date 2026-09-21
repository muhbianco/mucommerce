from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.tenancy.dns import DnsCheck
from app.tenancy.edge import build_traefik_config
from app.tenancy.models import DomainPurpose, DomainRole, DomainStatus, TenantDomain
from app.tenancy.service import TenantService
from tests.conftest import create_tenant


def _domain(tenant_id: str, hostname: str, role: str, purpose: str = "storefront") -> TenantDomain:
    return TenantDomain(
        tenant_id=tenant_id,
        hostname=hostname,
        kind="custom_apex",
        purpose=purpose,
        role=role,
        status=DomainStatus.ACTIVE,
    )


def test_primary_alias_and_chat_redirect() -> None:
    tenant = "0192a1b2-0000-7000-8000-000000000001"
    domains = [
        _domain(tenant, "lunares.com.br", DomainRole.PRIMARY),
        _domain(tenant, "www.lunares.com.br", DomainRole.ALIAS),
        _domain(tenant, "chat.lunares.com.br", DomainRole.ALIAS, DomainPurpose.CHAT_REDIRECT),
    ]
    config = build_traefik_config(domains, chatwoot_account_ids={tenant: 7})
    http = config["http"]

    web_routers = {k: v for k, v in http["routers"].items() if k.endswith("-web")}
    api_routers = {k: v for k, v in http["routers"].items() if k.endswith("-api")}
    assert len(web_routers) == 2 and len(api_routers) == 2

    for router in http["routers"].values():
        assert router["tls"] == {"certResolver": "letsencryptresolver"}
        assert router["entryPoints"] == ["websecure"]

    api_rules = {r["rule"] for r in api_routers.values()}
    assert "Host(`lunares.com.br`) && PathPrefix(`/api`)" in api_rules
    assert all(r["priority"] == 20 for r in api_routers.values())

    canonical = [
        m
        for m in http["middlewares"].values()
        if "redirectRegex" in m and m["redirectRegex"]["permanent"]
    ]
    assert len(canonical) == 1
    assert canonical[0]["redirectRegex"]["replacement"] == "https://lunares.com.br${1}"
    assert canonical[0]["redirectRegex"]["regex"].startswith("^https?://www\\.lunares\\.com\\.br")

    chat = [
        m
        for m in http["middlewares"].values()
        if "redirectRegex" in m and not m["redirectRegex"]["permanent"]
    ]
    assert chat[0]["redirectRegex"]["replacement"].endswith("/app/accounts/7/dashboard")

    assert http["services"]["commerce-web"]["loadBalancer"]["servers"][0]["url"].startswith(
        "http://"
    )


def test_empty_config_still_has_services() -> None:
    config = build_traefik_config([])
    assert config["http"]["routers"] == {}
    assert set(config["http"]["services"]) == {"commerce-web", "commerce-api"}


def test_router_names_do_not_collide_for_tenants_created_in_the_same_second() -> None:
    """Regression: names used tenant_id[:8], the UUIDv7 timestamp shared by close tenants."""
    tenant_a = "0192a1b2-c3d4-7000-8000-00000000000a"
    tenant_b = "0192a1b2-c3d4-7000-8000-00000000000b"
    domains = [
        _domain(tenant_a, "alpha.com.br", DomainRole.PRIMARY),
        _domain(tenant_b, "beta.com.br", DomainRole.PRIMARY),
    ]
    routers = build_traefik_config(domains)["http"]["routers"]
    assert len(routers) == 4
    rules = {r["rule"] for r in routers.values() if r["priority"] == 10}
    assert rules == {"Host(`alpha.com.br`)", "Host(`beta.com.br`)"}


def test_router_names_are_stable_when_other_domains_change() -> None:
    tenant = "0192a1b2-0000-7000-8000-000000000001"
    before = build_traefik_config([_domain(tenant, "zeta.com.br", DomainRole.PRIMARY)])
    after = build_traefik_config(
        [
            _domain(tenant, "zeta.com.br", DomainRole.PRIMARY),
            _domain(tenant, "alpha.zeta.com.br", DomainRole.ALIAS),
        ]
    )
    for name, router in before["http"]["routers"].items():
        assert after["http"]["routers"][name]["rule"] == router["rule"]


def test_static_hosts_stay_on_stack_labels(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "platform_base_domain", "loja.test")
    tenant = "0192a1b2-0000-7000-8000-000000000001"
    domains = [
        _domain(tenant, "loja.test", DomainRole.PRIMARY),
        _domain(tenant, "www.loja-modelo.com.br", DomainRole.ALIAS),
    ]
    http = build_traefik_config(domains)["http"]
    assert all("loja.test`)" not in r["rule"] for r in http["routers"].values())
    assert len(http["routers"]) == 2  # only the alias, redirecting to the static primary
    (canonical,) = http["middlewares"].values()
    assert canonical["redirectRegex"]["replacement"] == "https://loja.test${1}"


class _FailingVerifier:
    def __init__(self) -> None:
        self.calls = 0

    async def check(self, hostname: str, token: str) -> DnsCheck:
        del hostname, token
        self.calls += 1
        return DnsCheck(errors=["A não aponta para a plataforma"])


async def test_recheck_never_takes_a_static_host_offline(
    session_factory: async_sessionmaker[AsyncSession], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings, "platform_base_domain", "loja.test")
    tenant = await create_tenant(session_factory, "modelo")
    verifier = _FailingVerifier()
    async with session_factory() as session:
        static = TenantDomain(
            tenant_id=tenant.id,
            hostname="loja.test",
            kind="custom_subdomain",
            purpose=DomainPurpose.STOREFRONT,
            role=DomainRole.ALIAS,
            status=DomainStatus.ACTIVE,
        )
        custom = TenantDomain(
            tenant_id=tenant.id,
            hostname="modelo.com.br",
            kind="custom_apex",
            purpose=DomainPurpose.STOREFRONT,
            role=DomainRole.ALIAS,
            status=DomainStatus.ACTIVE,
        )
        session.add_all([static, custom])
        await session.flush()
        service = TenantService(session)
        for _ in range(3):
            await service.recheck_active_domain(static, verifier)  # type: ignore[arg-type]
            await service.recheck_active_domain(custom, verifier)  # type: ignore[arg-type]
        assert static.status == DomainStatus.ACTIVE and static.failed_checks == 0
        assert custom.status == DomainStatus.VERIFYING and custom.failed_checks == 3
        assert verifier.calls == 3  # the static host is not even looked up
