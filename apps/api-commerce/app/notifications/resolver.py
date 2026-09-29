"""De onde sai o e-mail desta loja: o SMTP dela, ou o caminho da plataforma.

A escolha é por loja e por entrega, não global. Duas lojas no mesmo processo mandam por contas
diferentes, e uma loja que ainda não configurou nada continua caindo no n8n da plataforma — ou
em lugar nenhum, que é o estado honesto de quem não configurou.

A decisão mora aqui, e não no job, porque o job não pode conhecer cofre nem configuração de
loja: ele reivindica a linha, entrega a alguém e grava o resultado.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import TenantNotFoundError
from app.core.logging import get_logger
from app.integrations.credentials import CredentialStore
from app.notifications.smtp import SmtpAccount, SmtpTransport
from app.notifications.transport import EmailTransport
from app.tenancy.resolver import TenantResolver
from app.tenancy.settings_schemas import email_settings

logger = get_logger(__name__)

PROVIDER = "smtp"


async def account_for(session: AsyncSession, tenant_id: str) -> SmtpAccount | None:
    """A conta de envio da loja, ou `None` quando ela não configurou (ou desligou)."""
    try:
        tenant = await TenantResolver(session).resolve_by_id(tenant_id)
    except TenantNotFoundError:
        # Loja apagada entre enfileirar e enviar: a entrega cai no caminho da plataforma em vez
        # de derrubar a fila de todas as outras.
        logger.warning("Loja da entrega não existe mais", extra={"tenant_id": tenant_id})
        return None
    cfg = email_settings(tenant.settings)
    if not cfg.enabled or not cfg.username:
        return None
    password = await CredentialStore(session, tenant_id).get(PROVIDER, "password")
    if not password:
        return None
    return SmtpAccount(
        host=cfg.host,
        port=cfg.port,
        username=cfg.username,
        password=password,
        from_name=cfg.from_name or tenant.name,
    )


async def transport_for(
    session: AsyncSession, tenant_id: str, fallback: EmailTransport
) -> EmailTransport:
    """O transporte desta loja. Sem SMTP configurado, vale o da plataforma."""
    account = await account_for(session, tenant_id)
    return SmtpTransport(account) if account is not None else fallback
