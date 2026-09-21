from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import dns.asyncresolver
import dns.exception
import dns.resolver

from app.core.config import settings

VERIFY_LABEL = "_muhbianco-verify"
TXT_PREFIX = "mb-verify="

Resolve = Callable[[str, str], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class DnsInstructions:
    txt_name: str
    txt_value: str
    a_records: list[str]
    cname_target: str
    apex: bool


@dataclass
class DnsCheck:
    txt_ok: bool = False
    target_ok: bool = False
    observed_txt: list[str] = field(default_factory=list)
    observed_a: list[str] = field(default_factory=list)
    observed_cname: str | None = None
    errors: list[str] = field(default_factory=list)


def instructions_for(hostname: str, token: str, apex: bool) -> DnsInstructions:
    return DnsInstructions(
        txt_name=f"{VERIFY_LABEL}.{hostname}",
        txt_value=f"{TXT_PREFIX}{token}",
        a_records=settings.edge_public_ip_list,
        cname_target=settings.edge_cname_target,
        apex=apex,
    )


RESOLVE_LIFETIME_SECONDS = 4.0


async def default_resolve(name: str, rdtype: str) -> list[str]:
    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = settings.dns_resolver_list or ["1.1.1.1"]
    resolver.lifetime = RESOLVE_LIFETIME_SECONDS
    answer = await resolver.resolve(name, rdtype)
    return [rdata.to_text().strip('"').rstrip(".") for rdata in answer]


class DnsVerifier:
    """Checks the ownership TXT and that the host points at our edge.

    `resolve` is injectable so tests never hit the network.
    """

    def __init__(self, resolve: Resolve | None = None) -> None:
        self._resolve: Resolve = resolve or default_resolve

    async def _query(self, name: str, rdtype: str) -> list[str]:
        try:
            return await self._resolve(name, rdtype)
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            return []
        except dns.exception.DNSException as exc:
            raise RuntimeError(f"{rdtype} {name}: {exc.__class__.__name__}") from exc

    async def check(self, hostname: str, token: str) -> DnsCheck:
        result = DnsCheck()
        expected_txt = f"{TXT_PREFIX}{token}"
        try:
            txts = await self._query(f"{VERIFY_LABEL}.{hostname}", "TXT")
            result.observed_txt = [t.replace('" "', "") for t in txts]
            result.txt_ok = expected_txt in result.observed_txt
        except RuntimeError as exc:
            result.errors.append(str(exc))

        try:
            cnames = await self._query(hostname, "CNAME")
            result.observed_cname = cnames[0].lower().rstrip(".") if cnames else None
        except RuntimeError as exc:
            result.errors.append(str(exc))
        try:
            result.observed_a = await self._query(hostname, "A")
        except RuntimeError as exc:
            result.errors.append(str(exc))

        target = settings.edge_cname_target.lower()
        ips = set(settings.edge_public_ip_list)
        if (result.observed_cname and result.observed_cname == target) or (
            ips and set(result.observed_a) & ips
        ):
            result.target_ok = True
        elif not ips and result.observed_a:
            # Edge IP not configured (dev): accept any A record only outside production.
            result.target_ok = settings.environment != "production"
        return result
