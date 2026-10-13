"""Uber Direct com a conta da própria loja (docs/15-entrega-uber-direct.md).

O que está aqui foi medido em 13/10/2026 pela sonda `scripts/uber_direct_probe.py`, não lido de
segunda mão:

- token: `POST auth.uber.com/oauth/v2/token`, `client_credentials`, escopo `eats.deliveries`,
  vale 30 dias, e a Uber limita a 100 pedidos de token por hora — por isso o token é guardado
  (fase 2), nunca pedido por chamada;
- o Customer ID entra no caminho (`/v1/customers/{id}/...`), e um Customer ID que não é destas
  chaves só aparece quando se usa: a listagem de entregas é o jeito barato de conferir;
- o `live_mode` da resposta **não** diz se as chaves são de teste (veio `false` em produção):
  quem diz é a loja, ao colar as chaves.

Fase 1 só confere as chaves. Cotar, chamar, consultar e cancelar entram na fase 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.credentials import CredentialStore

logger = get_logger(__name__)

#: Nome do provedor no cofre da loja.
PROVIDER = "uber_direct"
SECRET_KEYS = ("customer_id", "client_id", "client_secret")
SCOPE = "eats.deliveries"


@dataclass(frozen=True, slots=True)
class UberCredentials:
    customer_id: str
    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class UberCheck:
    """Resultado de conferir as chaves. `refused` diz qual chave a Uber recusou; nulo = ela não
    recusou nada (deu certo, ou não respondeu)."""

    ok: bool
    refused: Literal["client", "customer"] | None
    detail: str


async def load_credentials(session: AsyncSession, tenant_id: str) -> UberCredentials | None:
    """As chaves da loja, ou None se falta alguma. Só quem chama a Uber abre o cofre."""
    store = CredentialStore(session, tenant_id)
    valores: dict[str, str] = {}
    for chave in SECRET_KEYS:
        valor = await store.get(PROVIDER, chave)
        if not valor:
            return None
        valores[chave] = valor
    return UberCredentials(**valores)


async def save_credentials(
    session: AsyncSession, tenant_id: str, credentials: UberCredentials
) -> dict[str, str]:
    """Guarda as três chaves cifradas; devolve as formas mascaradas (para tela e auditoria)."""
    store = CredentialStore(session, tenant_id)
    return {
        chave: await store.put(PROVIDER, chave, getattr(credentials, chave))
        for chave in SECRET_KEYS
    }


async def forget_credentials(session: AsyncSession, tenant_id: str) -> None:
    store = CredentialStore(session, tenant_id)
    for chave in SECRET_KEYS:
        await store.delete(PROVIDER, chave)


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.uber_direct_timeout_seconds, connect=5.0)


async def check_credentials(credentials: UberCredentials) -> UberCheck:
    """Confere as chaves de verdade: pede um token e lista uma entrega com o Customer ID.

    Duas perguntas porque são dois erros diferentes: Client ID/Secret errados a Uber recusa no
    token; Customer ID de outra conta só aparece quando o token tenta usá-lo.
    """
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            token = await client.post(
                settings.uber_direct_auth_url,
                data={
                    "client_id": credentials.client_id,
                    "client_secret": credentials.client_secret,
                    "grant_type": "client_credentials",
                    "scope": SCOPE,
                },
            )
            if token.status_code in (400, 401, 403):
                return UberCheck(False, "client", "a Uber recusou o Client ID ou o Client Secret")
            if token.status_code != 200:
                return UberCheck(False, None, f"a Uber respondeu {token.status_code} ao token")
            acesso = str((token.json() or {}).get("access_token") or "")
            if not acesso:
                return UberCheck(False, None, "a Uber não devolveu o token")
            lista = await client.get(
                f"{settings.uber_direct_api_url.rstrip('/')}/v1/customers/"
                f"{credentials.customer_id}/deliveries",
                params={"limit": 1},
                headers={"Authorization": f"Bearer {acesso}"},
            )
    except httpx.HTTPError as exc:
        logger.warning("uber: sem resposta ao conferir chaves", extra={"error": type(exc).__name__})
        return UberCheck(False, None, "a Uber não respondeu agora")
    except ValueError:
        return UberCheck(False, None, "a Uber respondeu algo que não é JSON")
    if lista.status_code == 200:
        return UberCheck(True, None, "chaves conferidas")
    if lista.status_code in (401, 403, 404):
        return UberCheck(False, "customer", "o Customer ID não é destas chaves")
    return UberCheck(False, None, f"a Uber respondeu {lista.status_code} à conferência")
