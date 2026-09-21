from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from app.core.config import settings
from app.tenancy.models import DomainPurpose, DomainRole, TenantDomain

_SAFE = re.compile(r"[^a-z0-9-]")
# Same exclusion as the static routers in infra/docker-stack.yml: internal routes are reachable
# only from the internal network, never through a tenant host.
INTERNAL_API_PATHS = "`^/api/(v1|latest)/internal`"


def _name(*parts: str) -> str:
    return "-".join(_SAFE.sub("-", p.lower()) for p in parts if p)


def _host_key(hostname: str) -> str:
    """Stable, unique router prefix for a hostname.

    Derived from the hostname alone, so adding or removing another domain never renames
    existing routers. The digest keeps `a-b.com` and `a.b.com` from colliding after
    `_name` folds dots into dashes.
    """
    digest = hashlib.sha256(hostname.encode()).hexdigest()[:8]
    return _name(hostname, digest)


def build_traefik_config(
    domains: Iterable[TenantDomain],
    chatwoot_account_ids: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Traefik dynamic configuration (HTTP provider format) for every active tenant host.

    Per storefront host (`<key>` = `_host_key(hostname)`):
      - `<key>-web`: Host(`h`) → commerce-web (priority 10)
      - `<key>-api`: Host(`h`) && PathPrefix(`/api`), minus `/api/*/internal` → commerce-api
        (priority 20); internal paths fall to the web router and 404, as on the static hosts
      - alias hosts get a `redirectregex` middleware (308) to the primary host.
    Per chat_redirect host: 302 to the tenant's Chatwoot account.
    Hosts in `settings.static_edge_hosts` are skipped: the stack labels already route them.
    Certificates: `tls.certResolver` per router → HTTP-01 per host.
    """
    chatwoot_account_ids = chatwoot_account_ids or {}
    routers: dict[str, Any] = {}
    middlewares: dict[str, Any] = {}
    services: dict[str, Any] = {
        "commerce-web": {"loadBalancer": {"servers": [{"url": settings.edge_web_upstream}]}},
        "commerce-api": {"loadBalancer": {"servers": [{"url": settings.edge_api_upstream}]}},
    }

    static_hosts = settings.static_edge_hosts
    by_tenant: dict[str, list[TenantDomain]] = defaultdict(list)
    for domain in domains:
        by_tenant[domain.tenant_id].append(domain)

    for tenant_id, tenant_domains in by_tenant.items():
        primary = next(
            (
                d
                for d in tenant_domains
                if d.purpose == DomainPurpose.STOREFRONT and d.role == DomainRole.PRIMARY
            ),
            None,
        )
        tls = {"certResolver": settings.edge_cert_resolver}
        for domain in sorted(tenant_domains, key=lambda d: d.hostname):
            if domain.hostname in static_hosts:
                continue
            base = _host_key(domain.hostname)
            host_rule = f"Host(`{domain.hostname}`)"

            if domain.purpose == DomainPurpose.CHAT_REDIRECT:
                account_id = chatwoot_account_ids.get(tenant_id)
                target = (
                    f"{settings.chatwoot_public_url}/app/accounts/{account_id}/dashboard"
                    if account_id
                    else settings.chatwoot_public_url
                )
                middlewares[f"{base}-chat"] = {
                    "redirectRegex": {"regex": ".*", "replacement": target, "permanent": False}
                }
                routers[f"{base}-chat"] = {
                    "rule": host_rule,
                    "entryPoints": ["websecure"],
                    "service": "commerce-web",
                    "middlewares": [f"{base}-chat"],
                    "tls": tls,
                    "priority": 10,
                }
                continue

            router_middlewares: list[str] = []
            if primary is not None and domain.role == DomainRole.ALIAS:
                middlewares[f"{base}-canonical"] = {
                    "redirectRegex": {
                        "regex": f"^https?://{re.escape(domain.hostname)}(.*)",
                        "replacement": f"https://{primary.hostname}${{1}}",
                        "permanent": True,
                    }
                }
                router_middlewares.append(f"{base}-canonical")

            routers[f"{base}-web"] = {
                "rule": host_rule,
                "entryPoints": ["websecure"],
                "service": "commerce-web",
                "tls": tls,
                "priority": 10,
                **({"middlewares": router_middlewares} if router_middlewares else {}),
            }
            routers[f"{base}-api"] = {
                "rule": f"{host_rule} && PathPrefix(`/api`) && !PathRegexp({INTERNAL_API_PATHS})",
                "entryPoints": ["websecure"],
                "service": "commerce-api",
                "tls": tls,
                "priority": 20,
                **({"middlewares": router_middlewares} if router_middlewares else {}),
            }

    return {"http": {"routers": routers, "services": services, "middlewares": middlewares}}
