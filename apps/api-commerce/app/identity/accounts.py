"""MuhBianco accounts (api-agents) as the identity provider of the panel.

Sign-in is an authorization-code flow with PKCE: api-agents authenticates the person (Google,
password, Discord — its own login), then hands back a one-time code valid for 60 s. We redeem
the code server-to-server, over the internal network, together with the PKCE verifier only the
initiator knows. No secret is shared between the services and no api-agents token is trusted.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import settings
from app.core.exceptions import AuthenticationError, ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

REDEEM_PATH = "/api/v1/auth/commerce/redeem"


class _RedeemedAccount(BaseModel):
    account_id: str
    email: str
    full_name: str
    role: str
    email_verified: bool


@dataclass(frozen=True, slots=True)
class Account:
    account_id: str
    email: str
    full_name: str
    role: str  # api-agents role: admin | operator | viewer
    email_verified: bool

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "admin"


async def redeem_code(code: str, code_verifier: str) -> Account:
    """Exchange the one-time code for the account. Not retried: the code is single-use, so a
    retry after a lost response would fail anyway; the person just signs in again."""
    url = settings.muhbianco_accounts_internal_url.rstrip("/") + REDEEM_PATH
    try:
        async with httpx.AsyncClient(timeout=settings.muhbianco_accounts_timeout_seconds) as client:
            response = await client.post(url, json={"code": code, "code_verifier": code_verifier})
    except httpx.HTTPError as exc:
        logger.warning("MuhBianco accounts unreachable", extra={"error": type(exc).__name__})
        raise ExternalServiceError("Login MuhBianco indisponível no momento.") from exc
    if response.status_code in {400, 401, 404, 410, 422}:
        raise AuthenticationError("Código de login inválido ou expirado.")
    if response.status_code != 200:
        logger.warning("MuhBianco accounts error", extra={"status": response.status_code})
        raise ExternalServiceError("Login MuhBianco indisponível no momento.")
    try:
        data = _RedeemedAccount.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise ExternalServiceError("Resposta inesperada do login MuhBianco.") from exc
    return Account(
        account_id=data.account_id,
        email=data.email.strip().lower(),
        full_name=data.full_name.strip() or data.email,
        role=data.role,
        email_verified=data.email_verified,
    )
