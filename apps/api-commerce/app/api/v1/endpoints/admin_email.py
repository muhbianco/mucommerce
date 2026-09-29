"""Painel: de onde saem os e-mails desta loja.

Mesmo molde de Pagamentos, e pelo mesmo motivo: quem põe a senha decide de onde o e-mail sai,
então é `members_only` — a equipe da MuhBianco lê o estado para dar suporte, mas não grava a
credencial de ninguém.

A senha é **senha de app** do provedor, nunca a senha da conta. Ela entra cifrada no cofre e só
volta mascarada; o "enviar teste" é a única forma de saber se está certa, porque servidor de
e-mail não tem endpoint de "confira minha senha".
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.exceptions import ValidationError
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope
from app.integrations.credentials import CredentialStore, mask
from app.models.base import utcnow
from app.notifications.models import DeliveryStatus, NotificationDelivery
from app.notifications.resolver import PROVIDER, account_for
from app.notifications.transport import TransportError
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext
from app.tenancy.service import TenantService
from app.tenancy.settings_schemas import email_settings

router = APIRouter(prefix="/admin/tenants/{tenant_id}/email", tags=["Painel — E-mails"])

EmailReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))]
EmailOwner = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE, members_only=True))
]


class EmailConfigIn(StrictModel):
    enabled: bool = False
    host: Annotated[str, Field(min_length=3, max_length=200)] = "smtp.gmail.com"
    port: Annotated[int, Field(ge=1, le=65535)] = 587
    # Padrão simples de propósito: validar e-mail ao milímetro pediria uma dependência nova, e
    # quem recusa endereço torto de verdade é o servidor, na hora de enviar.
    username: (
        Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)] | None
    ) = None
    from_name: Annotated[str, Field(max_length=80)] = ""
    #: Omitida: mantém a que já está guardada. Trocar de conta pede senha nova.
    password: Annotated[str, Field(min_length=8, max_length=200)] | None = None


class EmailConfigRead(BaseModel):
    enabled: bool
    host: str
    port: int
    username: str
    from_name: str
    #: Só o suficiente para a pessoa reconhecer o que está guardado.
    password_masked: str | None = None
    password_changed_at: datetime | None = None
    last_test_ok: bool | None = None
    last_test_detail: str | None = None


class TestResult(BaseModel):
    ok: bool
    detail: str


async def _read(session: DbSession, tenant: TenantContext) -> EmailConfigRead:
    cfg = email_settings(tenant.settings)
    store = CredentialStore(session, tenant.id)
    guardadas = await store.status(PROVIDER)
    senha = guardadas.get("password")
    return EmailConfigRead(
        enabled=cfg.enabled,
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        from_name=cfg.from_name,
        password_masked=senha[0] if senha else None,
        password_changed_at=senha[1] if senha else None,
    )


@router.get("", response_model=EmailConfigRead, summary="De onde saem os e-mails da loja")
async def read_config(session: DbSession, user: CurrentAdmin, tenant: EmailReader) -> Any:
    return await _read(session, tenant)


@router.put("", response_model=EmailConfigRead, summary="Configura o envio (só a equipe da loja)")
async def save_config(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EmailOwner,
    body: EmailConfigIn,
) -> Any:
    store = CredentialStore(session, tenant.id)
    if body.password is not None:
        await store.put(PROVIDER, "password", body.password)
    guardadas = await store.status(PROVIDER)
    if body.enabled and not guardadas.get("password"):
        raise ValidationError(
            "Ligue o envio só depois de cadastrar a senha de app.", fields=["password"]
        )
    if body.enabled and not body.username:
        raise ValidationError("Sem a conta não há de onde enviar.", fields=["username"])
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    await service.set_setting(
        row,
        "email",
        {
            "enabled": body.enabled,
            "host": body.host,
            "port": body.port,
            "username": str(body.username or ""),
            "from_name": body.from_name,
        },
        admin_actor(request, user),
    )
    # O `tenant` em memória ficou velho depois da gravação: a resposta sai do que foi salvo.
    senha = guardadas.get("password")
    if body.password is not None:
        senha = (await store.status(PROVIDER)).get("password")
    return EmailConfigRead(
        enabled=body.enabled,
        host=body.host,
        port=body.port,
        username=str(body.username or ""),
        from_name=body.from_name,
        password_masked=senha[0] if senha else None,
        password_changed_at=senha[1] if senha else None,
    )


@router.post(
    "/test",
    response_model=TestResult,
    summary="Manda um e-mail de teste para quem está configurando",
    dependencies=[Depends(rate_limit("email_test", 5, 60))],
)
async def send_test(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: EmailOwner,
) -> TestResult:
    """A única prova de que a senha de app está certa.

    Servidor de e-mail não tem "confira minha senha": ou manda, ou recusa. O teste vai para a
    própria conta configurada — mandar para outra pessoa transformaria um teste em spam.
    """
    account = await account_for(session, tenant.id)
    if account is None:
        return TestResult(
            ok=False, detail="Cadastre a conta e a senha de app, e ligue o envio, antes de testar."
        )
    from app.notifications.smtp import SmtpTransport

    delivery = NotificationDelivery(
        tenant_id=tenant.id,
        template_key="email_test",
        recipient=account.username,
        subject=f"Teste de e-mail — {tenant.name}",
        body_text=(
            "Se você está lendo isto, os e-mails da sua loja já saem por esta conta.\n"
            "Nada mais precisa ser feito."
        ),
        body_html=None,
        status=DeliveryStatus.QUEUED,
        next_attempt_at=utcnow(),
    )
    try:
        await SmtpTransport(account).send(delivery)
    except TransportError as exc:
        return TestResult(ok=False, detail=str(exc)[:200])
    return TestResult(ok=True, detail=f"Enviado para {mask(account.username)}.")
