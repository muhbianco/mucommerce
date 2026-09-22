"""Panel: the store's payment providers (read by the team; written and tested by the owner)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope
from app.payments.config_service import PaymentConfigIn, PaymentConfigService, ProviderStatus
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/payments", tags=["Painel — Pagamentos"])

PaymentsReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.PAYMENTS_READ))]
PaymentsOwner = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.PAYMENTS_CONFIG, members_only=True))
]
Provider = Annotated[str, Path(pattern=r"^[a-z]{2,24}$")]


class SecretRead(BaseModel):
    masked: str
    changed_at: datetime


class ProviderRead(BaseModel):
    provider: str
    flag_on: bool  # the platform switched this provider on for the store
    enabled: bool
    is_default: bool
    sandbox: bool
    public_config: dict[str, Any]
    methods: list[str] | None
    installments_max: int
    secrets: dict[str, SecretRead]  # masked only
    missing: list[str]  # what is still needed to take payments
    webhook_url: str
    last_test_at: datetime | None
    last_test_ok: bool | None
    last_test_error: str | None
    last_webhook_at: datetime | None


def provider_read(status: ProviderStatus) -> ProviderRead:
    config = status.config
    return ProviderRead(
        provider=status.provider,
        flag_on=status.flag_on,
        enabled=bool(config and config.enabled),
        is_default=bool(config and config.is_default),
        sandbox=bool(config and config.sandbox),
        public_config=(config.public_config or {}) if config else {},
        methods=config.methods if config else None,
        installments_max=config.installments_max if config else 1,
        secrets={k: SecretRead(masked=m, changed_at=at) for k, (m, at) in status.secrets.items()},
        missing=status.missing,
        webhook_url=status.webhook_url,
        last_test_at=config.last_test_at if config else None,
        last_test_ok=config.last_test_ok if config else None,
        last_test_error=config.last_test_error if config else None,
        last_webhook_at=config.last_webhook_at if config else None,
    )


def _service(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: TenantContext
) -> PaymentConfigService:
    return PaymentConfigService(session, tenant, admin_actor(request, user))


@router.get("/providers", response_model=list[ProviderRead], summary="Meios de pagamento da loja")
async def list_providers(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: PaymentsReader
) -> list[ProviderRead]:
    return [provider_read(s) for s in await _service(request, session, user, tenant).overview()]


@router.put(
    "/providers/{provider}",
    response_model=ProviderRead,
    summary="Configura um meio de pagamento (só o dono; segredos omitidos ficam como estão)",
)
async def save_provider(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PaymentsOwner,
    provider: Provider,
    body: PaymentConfigIn,
) -> ProviderRead:
    return provider_read(await _service(request, session, user, tenant).save(provider, body))


@router.post(
    "/providers/{provider}/test",
    response_model=ProviderRead,
    summary="Testa as credenciais com o provedor",
    dependencies=[Depends(rate_limit("payments_test", 5, 60))],
)
async def test_provider(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: PaymentsOwner,
    provider: Provider,
) -> ProviderRead:
    return provider_read(await _service(request, session, user, tenant).test(provider))
