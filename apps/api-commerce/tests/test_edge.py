from __future__ import annotations

from app.tenancy.edge import build_traefik_config
from app.tenancy.models import DomainPurpose, DomainRole, DomainStatus, TenantDomain


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
