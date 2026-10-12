"""O token dos links de conta (confirmar cadastro, trocar senha), sem guardar segredo.

`token = HMAC(chave derivada do segredo do servidor, "propósito:id do desafio")`. O desafio guarda
só o id; a fila de e-mail guarda o corpo com `TOKEN_SLOT` no lugar do token, e o envio põe o
token na hora. Quem lê o banco não acha link que funcione.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from app.core.config import settings

TOKEN_SLOT = "@@MB_TOKEN@@"  # noqa: S105 (marcador, não segredo)
_KEY_DOMAIN = b"mucommerce.customer-email-link.v1"
#: Qual desafio cada e-mail com link carrega.
PURPOSE_BY_TEMPLATE = {"account_confirm": "signup", "password_reset": "reset"}


def _key() -> bytes:
    secret = settings.jwt_secret.get_secret_value().encode()
    return hmac.new(secret, _KEY_DOMAIN, hashlib.sha256).digest()


def challenge_token(purpose: str, challenge_id: str) -> str:
    digest = hmac.new(_key(), f"{purpose}:{challenge_id}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:24]).rstrip(b"=").decode()


def token_matches(purpose: str, challenge_id: str, token: str) -> bool:
    return hmac.compare_digest(challenge_token(purpose, challenge_id), token)


def fill_token(text: str | None, template_key: str, challenge_id: str) -> str | None:
    """O corpo guardado na fila com o token de verdade no lugar do marcador."""
    purpose = PURPOSE_BY_TEMPLATE.get(template_key)
    if text is None or purpose is None:
        return text
    return text.replace(TOKEN_SLOT, challenge_token(purpose, challenge_id))
