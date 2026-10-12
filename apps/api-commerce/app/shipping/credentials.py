"""As credenciais de envio de uma loja, para qualquer provedor (docs/14-pedidos-lunares.md §4).

Antes, cada ponto (cotação, despacho, rastreio, tela) montava `{"access_token": ...}` à mão; com
um segundo provedor isso viraria sete lugares para esquecer. Aqui o provedor diz o que precisa
(`capabilities.required_secrets`) e o cofre da loja entrega. O que é da plataforma (o token de
parceiro da Frenet) o próprio provedor lê da configuração: nunca mora no cofre da loja.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.credentials import CredentialStore
from app.shipping import registry
from app.shipping.provider import ShippingCredentials

#: O segredo que a loja cola na tela de Envio. Melhor Envio e Frenet pedem um só, o token da conta.
STORE_SECRET = "access_token"  # noqa: S105 (nome da chave no cofre)


async def load_credentials(
    session: AsyncSession, tenant_id: str, provider_name: str
) -> ShippingCredentials | None:
    """As credenciais da loja para `provider_name`, ou None se falta alguma."""
    provider = registry.get_provider(provider_name)
    if provider is None:
        return None
    store = CredentialStore(session, tenant_id)
    secrets: dict[str, str] = {}
    for key in provider.capabilities.required_secrets:
        value = await store.get(provider_name, key)
        if not value:
            return None
        secrets[key] = value
    return ShippingCredentials(
        secrets=secrets,
        public_config={},
        sandbox=settings.environment != "production",
    )


async def is_connected(session: AsyncSession, tenant_id: str, provider_name: str) -> bool:
    """A loja colou o token deste provedor (sem decifrar nada)."""
    provider = registry.get_provider(provider_name)
    if provider is None:
        return False
    stored = await CredentialStore(session, tenant_id).status(provider_name)
    return all(key in stored for key in provider.capabilities.required_secrets)
