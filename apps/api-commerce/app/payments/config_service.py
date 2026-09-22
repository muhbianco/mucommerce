"""A store's payment providers: configuration by the owner, status for everyone else.

Only a member with `payments:config` (the owner; ADR 0011 §8) writes, and platform staff never do
(`members_only` on the route): whoever sets the access token decides where the money goes.
Secrets are write-only: responses and the audit log carry the masked form.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.core.config import settings
from app.core.exceptions import FeatureDisabledError, NotFoundError, ValidationError
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
from app.payments import registry
from app.payments.models import TenantPaymentConfig
from app.payments.provider import ProviderCredentials
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

Secret = Annotated[str, Field(min_length=1, max_length=500)]
PublicValue = Annotated[str, Field(min_length=1, max_length=200)]
# Public settings each provider accepts (anything else is refused).
PUBLIC_KEYS = {"mercadopago": {"public_key"}, "infinitepay": {"handle"}, "fake": set()}


class SecretsIn(StrictModel):
    access_token: Secret | None = None
    webhook_secret: Secret | None = None


class PaymentConfigIn(StrictModel):
    enabled: bool = False
    is_default: bool = False
    sandbox: bool = False
    public_config: dict[str, PublicValue] = Field(default_factory=dict)
    methods: Annotated[list[Literal["pix", "card", "link"]], Field(max_length=3)] | None = None
    installments_max: Annotated[int, Field(ge=1, le=12)] = 1
    # Omitted keys keep the stored secret.
    credentials: SecretsIn = SecretsIn()


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    flag_on: bool
    config: TenantPaymentConfig | None
    secrets: dict[str, tuple[str, datetime]]  # key → (masked, changed)
    missing: list[str]
    webhook_url: str


class PaymentConfigService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.store = CredentialStore(session, tenant.id)

    def webhook_url(self, provider: str) -> str:
        return f"https://{settings.api_public_host}/api/v1/webhooks/{provider}/{self.tenant.public_key}"

    async def configs(self) -> list[TenantPaymentConfig]:
        stmt = select(TenantPaymentConfig).order_by(TenantPaymentConfig.provider).limit(10)
        return list((await self.session.execute(stmt)).scalars())

    async def overview(self) -> list[ProviderStatus]:
        rows = {c.provider: c for c in await self.configs()}
        return [await self._status(name, rows.get(name)) for name in registry.allowed()]

    async def save(self, provider_name: str, data: PaymentConfigIn) -> ProviderStatus:
        provider = registry.get_provider(provider_name)
        if provider is None:
            raise NotFoundError("Meio de pagamento indisponível.")
        if not registry.flag_on(self.tenant, provider_name):
            raise FeatureDisabledError(features=[f"payments.{provider_name}"])
        unknown = sorted(set(data.public_config) - PUBLIC_KEYS.get(provider_name, set()))
        if unknown:
            raise ValidationError("Configuração pública desconhecida.", fields=unknown)
        config = await self._row(provider_name)
        if config is None:
            config = TenantPaymentConfig(provider=provider_name)
            self.session.add(config)
        masked: dict[str, str] = {}
        for key, value in data.credentials.model_dump(exclude_none=True).items():
            masked[key] = await self.store.put(provider_name, key, value)
        config.enabled = data.enabled
        config.is_default = data.is_default
        config.sandbox = data.sandbox
        config.public_config = dict(data.public_config) or None
        config.methods = list(data.methods) if data.methods else None
        config.installments_max = data.installments_max
        config.configured_at = utcnow()
        config.configured_by_actor = self.actor.id
        await self.session.flush()
        secrets = set((await self.store.status(provider_name)).keys())
        missing = registry.missing_setup(provider, config, secrets)
        if config.enabled and missing:
            raise ValidationError(
                "Faltam dados para ativar este meio de pagamento.", missing=missing
            )
        if config.is_default:
            for other in await self.configs():
                if other.id != config.id:
                    other.is_default = False
        await self.session.flush()
        await audit(
            self.session,
            actor=self.actor.id,
            action="payment_config.saved",
            entity_type="tenant_payment_config",
            entity_id=config.id,
            tenant_id=self.tenant.id,
            after={
                "provider": provider_name,
                "enabled": config.enabled,
                "is_default": config.is_default,
                "sandbox": config.sandbox,
                "methods": config.methods,
                "public_config": config.public_config,
                "secrets_changed": masked,  # masked values only
            },
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="tenant",
            aggregate_id=self.tenant.id,
            event_type="tenant.payment_config_changed",
            payload={"provider": provider_name, "enabled": config.enabled},
            tenant_id=self.tenant.id,
        )
        return await self._status(provider_name, config)

    async def test(self, provider_name: str) -> ProviderStatus:
        provider = registry.get_provider(provider_name)
        config = await self._row(provider_name)
        if provider is None or config is None:
            raise NotFoundError("Meio de pagamento não configurado.")
        creds = await self.credentials(provider_name, config)
        await self.session.commit()  # no transaction open while the provider answers
        result = await provider.test_credentials(creds)
        config = await self._row(provider_name)
        assert config is not None
        config.last_test_at = utcnow()
        config.last_test_ok = result.ok
        config.last_test_error = None if result.ok else (result.detail or "falhou")[:300]
        await self.session.flush()
        return await self._status(provider_name, config)

    async def credentials(
        self, provider_name: str, config: TenantPaymentConfig | None = None
    ) -> ProviderCredentials:
        """Decrypted, for the payment service's provider calls only."""
        config = config or await self._row(provider_name)
        secrets: dict[str, str] = {}
        for key in await self.store.status(provider_name):
            value = await self.store.get(provider_name, key)
            if value is not None:
                secrets[key] = value
        return ProviderCredentials(
            secrets=secrets,
            public_config=(config.public_config or {}) if config else {},
            sandbox=bool(config and config.sandbox),
        )

    async def ops_summary(self) -> list[dict[str, Any]]:
        """For the site admin: configured or not, and the last test/webhook — no secrets, not
        even masked ones."""
        return [
            {
                "provider": status.provider,
                "flag_on": status.flag_on,
                "enabled": bool(status.config and status.config.enabled),
                "configured": not status.missing,
                "last_test_at": status.config.last_test_at if status.config else None,
                "last_test_ok": status.config.last_test_ok if status.config else None,
                "last_webhook_at": status.config.last_webhook_at if status.config else None,
            }
            for status in await self.overview()
        ]

    async def _row(self, provider: str) -> TenantPaymentConfig | None:
        stmt = select(TenantPaymentConfig).where(TenantPaymentConfig.provider == provider)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _status(self, name: str, config: TenantPaymentConfig | None) -> ProviderStatus:
        provider = registry.get_provider(name)
        secrets = await self.store.status(name)
        missing = (
            registry.missing_setup(provider, config, set(secrets))
            if provider is not None and config is not None
            else ["config"]
        )
        return ProviderStatus(
            provider=name,
            flag_on=registry.flag_on(self.tenant, name),
            config=config,
            secrets=secrets,
            missing=missing,
            webhook_url=self.webhook_url(name),
        )
