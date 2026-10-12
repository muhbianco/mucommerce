from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class StorefrontTenant(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    timezone: str
    locale: str
    currency: str


class PanelHostRead(BaseModel):
    """Loja dona de um host de painel (<slug>.painel.* ou painel.<domínio da loja>)."""

    tenant_id: str
    slug: str
    name: str
    host: str


class StorefrontContext(BaseModel):
    """What the Next.js middleware/server needs to render a tenant. No secrets."""

    tenant: StorefrontTenant
    host: str | None
    primary_host: str | None
    access_mode: str
    #: Loja fechada "toda fechada": nem a página inicial antes de entrar (docs/14 §3).
    landing_locked: bool = False
    #: A loja fechada tem senha: a porta oferece "tenho a senha" ao lado do Google.
    password_gate: bool = False
    features: dict[str, bool]
    branding: dict[str, Any]
    seo: dict[str, Any]
    fulfillment: dict[str, Any]
    #: Meios aceitos e teto de parcelamento — a vitrine anuncia "em até 12x" antes do carrinho.
    payments: dict[str, Any] = {}
    #: O que o checkout pede além do básico (hoje: se o WhatsApp do cliente é obrigatório).
    checkout: dict[str, Any] = {}
    chatwoot_url: str | None = None
