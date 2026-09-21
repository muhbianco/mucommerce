from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import StrictModel


class TenantCreate(StrictModel):
    slug: str = Field(min_length=3, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
    name: str = Field(min_length=2, max_length=160)
    legal_name: str | None = Field(default=None, max_length=200)
    document: str | None = Field(default=None, max_length=20)
    timezone: str = Field(default="America/Sao_Paulo", max_length=64)
    plan: str = Field(default="standard", max_length=32)


class TenantRead(BaseModel):
    id: str
    slug: str
    public_key: str
    name: str
    legal_name: str | None
    document: str | None
    status: str
    plan: str
    default_currency: str
    timezone: str
    locale: str
    activated_at: datetime | None
    created_at: datetime


class TenantOwnerRead(BaseModel):
    admin_user_id: str
    account_id: str | None
    email: str
    full_name: str
    role: str


class TenantListItem(TenantRead):
    """List row for the MuhBianco admin "Lojas" page: tenant plus its owners and address."""

    primary_host: str | None = None
    access_mode: str = "whitelist"
    owners: list[TenantOwnerRead] = Field(default_factory=list)


class TenantOwnerSet(StrictModel):
    """The store owner is a MuhBianco account (api-agents user), picked in the site admin."""

    account_id: str = Field(min_length=36, max_length=36)
    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    full_name: str = Field(default="", max_length=200)


class TenantStatusChange(StrictModel):
    status: str = Field(pattern=r"^(provisioning|active|suspended|archived|draft)$")
    reason: str | None = Field(default=None, max_length=200)


class FeatureFlagsUpdate(StrictModel):
    flags: dict[str, bool]


class SettingUpdate(StrictModel):
    value: dict[str, Any]


class DomainCreate(StrictModel):
    hostname: str = Field(min_length=3, max_length=253)
    purpose: str = Field(default="storefront", pattern=r"^(storefront|chat_redirect)$")
    role: str = Field(default="alias", pattern=r"^(primary|alias)$")


class DnsInstructionsRead(BaseModel):
    txt_name: str
    txt_value: str
    a_records: list[str]
    cname_target: str
    apex: bool


class DomainRead(BaseModel):
    id: str
    tenant_id: str
    hostname: str
    kind: str
    purpose: str
    role: str
    status: str
    tls_status: str
    verified_at: datetime | None
    last_check_at: datetime | None
    last_error: str | None
    instructions: DnsInstructionsRead | None = None


class DomainCheckRead(BaseModel):
    domain: DomainRead
    txt_ok: bool
    target_ok: bool
    observed_a: list[str]
    observed_cname: str | None
    errors: list[str]


class OutboxEventRead(BaseModel):
    id: str
    tenant_id: str | None
    aggregate_type: str
    aggregate_id: str
    event_type: str
    status: str
    attempts: int
    occurred_at: datetime
    last_error: str | None
