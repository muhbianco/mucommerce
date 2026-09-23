from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.common import StrictModel
from app.tenancy.models import Tenant

# The api-agents subscription key (`<service_code>:<user_id>`): theirs to shape, ours to store.
SubscriptionRef = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")


class StoreReserve(StrictModel):
    subscription_ref: str = SubscriptionRef
    account_id: str = Field(min_length=8, max_length=64)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    full_name: str = Field(min_length=1, max_length=160)
    slug: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    name: str = Field(min_length=2, max_length=160)


class StoreBillingState(StrictModel):
    state: Literal["active", "suspended"]
    # When the store stops serving. The catalog decides it (3 days after a failed renewal);
    # a suspension with no deadline takes the storefront down right away.
    grace_until: datetime | None = None
    reason: str | None = Field(default=None, max_length=120)


class StoreRead(BaseModel):
    tenant_id: str
    slug: str
    name: str
    status: str
    subscription_ref: str | None
    billing_grace_until: datetime | None
    storefront_url: str
    panel_url: str

    @classmethod
    def of(cls, tenant: Tenant) -> StoreRead:
        return cls(
            tenant_id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            subscription_ref=tenant.subscription_ref,
            billing_grace_until=tenant.billing_grace_until,
            storefront_url=f"https://{tenant.slug}.{settings.platform_base_domain}",
            panel_url=f"https://{settings.panel_host}/t/{tenant.id}",
        )
