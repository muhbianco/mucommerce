"""Which payment providers exist, which this deployment allows, and which a store offers.

A store offers a provider when: the deployment allows it (PAYMENTS_ALLOWED_PROVIDERS), the
store's flag `payments.<provider>` is on (the fake provider needs no flag: it only exists where
the deployment allows it, i.e. tests and E2E), the store enabled its config and every required
credential and public setting is present.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.payments.models import TenantPaymentConfig
from app.payments.provider import PaymentMethod, PaymentProvider
from app.payments.providers.fake import FakeProvider
from app.payments.providers.infinitepay import InfinitePayProvider
from app.payments.providers.mercadopago import MercadoPagoProvider
from app.tenancy.context import TenantContext

_PROVIDERS: dict[str, PaymentProvider] = {
    "fake": FakeProvider(),
    "mercadopago": MercadoPagoProvider(),
    "infinitepay": InfinitePayProvider(),
}


def register(provider: PaymentProvider) -> None:
    _PROVIDERS[provider.name] = provider


def allowed() -> list[str]:
    return [name for name in settings.payments_allowed_provider_list if name in _PROVIDERS]


def get_provider(name: str) -> PaymentProvider | None:
    return _PROVIDERS.get(name) if name in allowed() else None


def flag_on(tenant: TenantContext, provider: str) -> bool:
    return provider == "fake" or tenant.feature(f"payments.{provider}")


@dataclass(frozen=True, slots=True)
class PaymentOption:
    provider: str
    methods: tuple[PaymentMethod, ...]
    mode: str
    is_default: bool
    public_config: Mapping[str, Any]
    installments_max: int


def missing_setup(
    provider: PaymentProvider, config: TenantPaymentConfig, secrets: set[str]
) -> list[str]:
    """What the store still has to configure before this provider can take payments."""
    public = config.public_config or {}
    missing = [f"secret:{k}" for k in provider.capabilities.required_secrets if k not in secrets]
    missing += [f"public:{k}" for k in provider.capabilities.required_public if not public.get(k)]
    return missing


def options_for(
    tenant: TenantContext,
    configs: list[TenantPaymentConfig],
    secrets: Mapping[str, set[str]],
) -> list[PaymentOption]:
    options: list[PaymentOption] = []
    for config in configs:
        provider = get_provider(config.provider)
        if provider is None or not config.enabled or not flag_on(tenant, config.provider):
            continue
        if missing_setup(provider, config, secrets.get(config.provider, set())):
            continue
        methods = tuple(
            m for m in provider.capabilities.methods if not config.methods or m in config.methods
        )
        if not methods:
            continue
        options.append(
            PaymentOption(
                provider=config.provider,
                methods=methods,
                mode=provider.capabilities.mode,
                is_default=config.is_default,
                public_config=config.public_config or {},
                installments_max=config.installments_max,
            )
        )
    return sorted(options, key=lambda o: (not o.is_default, o.provider))
