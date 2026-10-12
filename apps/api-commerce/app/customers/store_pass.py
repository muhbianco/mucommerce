"""O passe de quem acertou a senha da loja (docs/14-pedidos-lunares.md §3).

Loja fechada pode ter uma senha que o lojista passa adiante. Quem acerta recebe o passe: um
cookie `__Host-mb_pass` com `tenant.versão.validade.assinatura`. Ele abre a vitrine sem login;
o login continua exigido no carrinho.

- **Preso à loja**: a assinatura cobre o id da loja, então o passe de uma não abre outra.
- **Preso à versão da senha**: trocar ou remover a senha sobe `storefront.password_version`, e
  todo passe antigo para de valer na hora. É assim que o lojista tranca todo mundo de novo.
- **Sem estado**: a conferência é só HMAC contra a configuração da loja (que já vem no contexto),
  sem consulta ao banco a cada página.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings

PASS_COOKIE = "__Host-mb_pass"  # noqa: S105 (nome do cookie, não segredo)
PASS_TTL = timedelta(days=30)
#: Onde a senha mora no cofre (`tenant_integration_credentials`, cifrada por loja).
STORE_PASSWORD_PROVIDER = "storefront"  # noqa: S105 (nome do provedor no cofre)
STORE_PASSWORD_KEY = "password"  # noqa: S105 (nome da chave no cofre)
_KEY_DOMAIN = b"mucommerce.store-pass.v1"
MAX_TOKEN_LENGTH = 160


def _key() -> bytes:
    secret = settings.jwt_secret.get_secret_value().encode()
    return hmac.new(secret, _KEY_DOMAIN, hashlib.sha256).digest()


def _sign(tenant_id: str, version: int, expires: int) -> str:
    digest = hmac.new(_key(), f"{tenant_id}.{version}.{expires}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def password_version(storefront: dict[str, Any] | None) -> int:
    """A versão vigente da senha; 0 = a loja nunca teve senha."""
    try:
        return int((storefront or {}).get("password_version") or 0)
    except (TypeError, ValueError):
        return 0


def password_enabled(storefront: dict[str, Any] | None) -> bool:
    return bool((storefront or {}).get("password_set"))


def issue(tenant_id: str, version: int, now: datetime) -> tuple[str, datetime]:
    expires_at = now + PASS_TTL
    expires = int(expires_at.timestamp())
    return f"{tenant_id}.{version}.{expires}.{_sign(tenant_id, version, expires)}", expires_at


def is_valid(
    token: str | None, tenant_id: str, storefront: dict[str, Any] | None, now: datetime
) -> bool:
    """O passe vale nesta loja, com a senha de agora, e ainda não venceu."""
    if not token or len(token) > MAX_TOKEN_LENGTH or not password_enabled(storefront):
        return False
    parts = token.split(".")
    if len(parts) != 4:
        return False
    owner, raw_version, raw_expires, signature = parts
    try:
        version, expires = int(raw_version), int(raw_expires)
    except ValueError:
        return False
    if owner != tenant_id or version != password_version(storefront):
        return False
    if expires <= int(now.timestamp()):
        return False
    return hmac.compare_digest(signature, _sign(owner, version, expires))
