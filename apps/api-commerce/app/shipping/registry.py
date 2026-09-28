"""Quais transportadoras existem, quais este ambiente permite e qual a loja oferece.

Mesma regra de pagamentos (ADR 0005): o ambiente decide o que existe
(`SHIPPING_ALLOWED_PROVIDERS`), a flag `shipping.<provider>` decide se a loja pode, e a
configuração da loja decide se está ligada e completa. O `fake` não precisa de flag — ele só
existe onde o ambiente permite, isto é, em teste e E2E.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.config import settings
from app.shipping.provider import ShippingProvider
from app.shipping.providers.fake import FakeShippingProvider
from app.shipping.providers.melhorenvio import MelhorEnvioProvider
from app.tenancy.context import TenantContext

_PROVIDERS: dict[str, ShippingProvider] = {
    "fake": FakeShippingProvider(),
    "melhorenvio": MelhorEnvioProvider(),
}


def register(provider: ShippingProvider) -> None:
    _PROVIDERS[provider.name] = provider


def allowed() -> list[str]:
    return [name for name in settings.shipping_allowed_provider_list if name in _PROVIDERS]


def get_provider(name: str) -> ShippingProvider | None:
    return _PROVIDERS.get(name) if name in allowed() else None


def flag_on(tenant: TenantContext, provider: str) -> bool:
    return provider == "fake" or tenant.feature(f"shipping.{provider}")


def missing_setup(
    provider: ShippingProvider, *, secrets: set[str], public: Mapping[str, Any]
) -> list[str]:
    """O que ainda falta configurar antes desta transportadora cotar."""
    faltando = [f"secret:{k}" for k in provider.capabilities.required_secrets if k not in secrets]
    faltando += [f"public:{k}" for k in provider.capabilities.required_public if not public.get(k)]
    return faltando
