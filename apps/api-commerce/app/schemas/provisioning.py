from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.common import StrictModel
from app.tenancy.dns import instructions_for
from app.tenancy.models import DomainKind, DomainPurpose, Tenant, TenantDomain

# The api-agents subscription key (`<service_code>:<user_id>`): theirs to shape, ours to store.
SubscriptionRef = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")


class StoreReserve(StrictModel):
    subscription_ref: str = SubscriptionRef
    account_id: str = Field(min_length=8, max_length=64)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    full_name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    name: str = Field(min_length=2, max_length=160)
    # The customer's own address (`loja.empresa.com.br` or `empresa.com.br`), chosen at purchase.
    # It comes in the reservation on purpose: a hostname already taken must answer 409 *before*
    # the wallet is debited.
    custom_domain: str | None = Field(default=None, min_length=4, max_length=253)


class StoreBillingState(StrictModel):
    state: Literal["active", "suspended"]
    # When the store stops serving. The catalog decides it (3 days after a failed renewal);
    # a suspension with no deadline takes the storefront down right away.
    grace_until: datetime | None = None
    reason: str | None = Field(default=None, max_length=120)


class DomainState(BaseModel):
    hostname: str
    role: str
    status: str
    # Only for the customer's own domain: what they (or MuhBianco) must create in the DNS zone.
    txt_name: str | None = None
    txt_value: str | None = None
    cname_target: str | None = None
    a_records: list[str] = []
    apex: bool = False

    @classmethod
    def of(cls, domain: TenantDomain) -> DomainState:
        platform = domain.kind == DomainKind.PLATFORM_SUBDOMAIN
        dns = (
            None
            if platform
            else instructions_for(
                domain.hostname, domain.verification_token, domain.kind == DomainKind.CUSTOM_APEX
            )
        )
        return cls(
            hostname=domain.hostname,
            role=domain.role,
            status=domain.status,
            txt_name=dns.txt_name if dns else None,
            txt_value=dns.txt_value if dns else None,
            cname_target=dns.cname_target if dns else None,
            a_records=dns.a_records if dns else [],
            apex=bool(dns and dns.apex),
        )


class StoreRead(BaseModel):
    tenant_id: str
    slug: str
    name: str
    status: str
    subscription_ref: str | None
    billing_grace_until: datetime | None
    storefront_url: str
    panel_url: str
    # The address the platform always gives the store. It answers as soon as `*.loja` resolves.
    platform_host: str
    # The customer's own domain, when they chose one, with the records still to be created.
    custom_domain: DomainState | None
    domains: list[DomainState]

    @classmethod
    def of(cls, tenant: Tenant) -> StoreRead:
        platform_host = f"{tenant.slug}.{settings.platform_base_domain}"
        storefront = [d for d in tenant.domains if d.purpose == DomainPurpose.STOREFRONT]
        custom = next(
            (d for d in storefront if d.kind != DomainKind.PLATFORM_SUBDOMAIN),
            None,
        )
        return cls(
            tenant_id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            subscription_ref=tenant.subscription_ref,
            billing_grace_until=tenant.billing_grace_until,
            storefront_url=f"https://{platform_host}",
            panel_url=f"https://{settings.panel_host}/t/{tenant.id}",
            platform_host=platform_host,
            custom_domain=DomainState.of(custom) if custom else None,
            domains=[DomainState.of(d) for d in storefront],
        )
