"""Google OpenID Connect for store customers: authorize URL, code exchange, id_token checks.

Authorization code + PKCE (S256) with a nonce. The id_token comes straight from Google's token
endpoint over TLS, and its RS256 signature is still checked against Google's published keys,
along with iss, aud, exp, iat and `email_verified`. The key set is fetched with httpx (timeout),
cached per process for the `max-age` Google sends (clamped), refetched at most once a minute
when an unknown key id shows up, and the last good set is kept if a refresh fails.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.core.config import GOOGLE_ISSUER, settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Google documents both forms of the issuer in its id_tokens.
GOOGLE_ISSUERS = (GOOGLE_ISSUER, "accounts.google.com")
CLOCK_SKEW_SECONDS = 60
JWKS_MIN_TTL = 300
JWKS_MAX_TTL = 86_400
JWKS_REFETCH_EVERY = 60
_MAX_AGE = re.compile(r"max-age=(\d+)")


class OidcError(Exception):
    """Sign-in could not complete. `code` is safe to show in a URL (`/entrar?erro=<code>`)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str
    email_verified: bool
    name: str | None
    nonce: str | None
    claims: dict[str, Any]


class _KeyCache:
    def __init__(self) -> None:
        self.keys: jwt.PyJWKSet | None = None
        self.expires_at = 0.0
        self.fetched_at = 0.0
        self.lock = asyncio.Lock()

    def clear(self) -> None:
        self.keys, self.expires_at, self.fetched_at = None, 0.0, 0.0


_keys = _KeyCache()


def reset_key_cache() -> None:
    """Tests only: forget the cached key set."""
    _keys.clear()


def _issuers() -> tuple[str, ...]:
    issuer = settings.google_oidc_issuer
    return GOOGLE_ISSUERS if issuer == GOOGLE_ISSUER else (issuer,)


def authorize_url(*, state: str, nonce: str, code_challenge: str) -> str:
    query = urlencode(
        {
            "client_id": settings.google_customer_client_id,
            "redirect_uri": settings.customer_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
    )
    return f"{settings.google_oidc_authorize_url}?{query}"


async def exchange_code(code: str, code_verifier: str) -> str:
    """Trade the authorization code for the id_token. Not retried: codes are single use."""
    data = {
        "code": code,
        "client_id": settings.google_customer_client_id,
        "client_secret": settings.google_customer_client_secret.get_secret_value(),
        "redirect_uri": settings.customer_redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=settings.google_oidc_timeout_seconds) as client:
            response = await client.post(settings.google_oidc_token_url, data=data)
    except httpx.HTTPError as exc:
        logger.warning("Google token endpoint unreachable", extra={"error": type(exc).__name__})
        raise OidcError("google_indisponivel") from exc
    if 400 <= response.status_code < 500:
        # invalid_grant & co: expired, reused or forged code. Never log the body (it echoes input).
        logger.info("Google refused the code", extra={"status": response.status_code})
        raise OidcError("login_invalido")
    if response.status_code != 200:
        logger.warning("Google token endpoint error", extra={"status": response.status_code})
        raise OidcError("google_indisponivel")
    try:
        id_token = response.json()["id_token"]
    except (ValueError, KeyError, TypeError) as exc:
        raise OidcError("google_indisponivel", "token response without id_token") from exc
    if not isinstance(id_token, str):
        raise OidcError("google_indisponivel", "id_token is not a string")
    return id_token


async def _fetch_keys() -> None:
    async with httpx.AsyncClient(timeout=settings.google_oidc_timeout_seconds) as client:
        response = await client.get(settings.google_oidc_jwks_url)
    response.raise_for_status()
    keys = jwt.PyJWKSet.from_dict(response.json())
    match = _MAX_AGE.search(response.headers.get("cache-control", ""))
    ttl = int(match.group(1)) if match else JWKS_MIN_TTL
    now = time.monotonic()
    _keys.keys = keys
    _keys.fetched_at = now
    _keys.expires_at = now + min(max(ttl, JWKS_MIN_TTL), JWKS_MAX_TTL)


async def _signing_key(kid: str) -> jwt.PyJWK:
    async with _keys.lock:
        now = time.monotonic()
        known = _keys.keys is not None and kid in {k.key_id for k in _keys.keys.keys}
        stale = now >= _keys.expires_at
        may_refetch = now - _keys.fetched_at >= JWKS_REFETCH_EVERY or _keys.keys is None
        if stale or (not known and may_refetch):
            try:
                await _fetch_keys()
            except (httpx.HTTPError, ValueError, jwt.PyJWKSetError) as exc:
                logger.warning("Google keys refresh failed", extra={"error": type(exc).__name__})
                if _keys.keys is None:
                    raise OidcError("google_indisponivel") from exc
        if _keys.keys is None:
            raise OidcError("google_indisponivel")
        try:
            return _keys.keys[kid]
        except KeyError as exc:
            raise OidcError("login_invalido", "unknown key id") from exc


async def verify_id_token(id_token: str) -> GoogleIdentity:
    """Signature, issuer, audience and times. The caller checks the nonce against its flow."""
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OidcError("login_invalido", "malformed id_token") from exc
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise OidcError("login_invalido", "unexpected id_token header")
    key = await _signing_key(header["kid"])
    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key=key.key,
            algorithms=["RS256"],
            audience=settings.google_customer_client_id,
            issuer=_issuers(),
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["iss", "aud", "sub", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise OidcError("login_invalido", type(exc).__name__) from exc

    email = claims.get("email")
    verified = claims.get("email_verified") in (True, "true")
    if not isinstance(email, str) or not email or not verified:
        raise OidcError("email_nao_verificado")
    name = claims.get("name")
    nonce = claims.get("nonce")
    return GoogleIdentity(
        subject=str(claims["sub"]),
        email=email.strip().lower(),
        email_verified=True,
        name=name.strip() if isinstance(name, str) and name.strip() else None,
        nonce=nonce if isinstance(nonce, str) else None,
        claims=claims,
    )
