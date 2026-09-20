from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.core.ids import new_id

_password_hash = PasswordHash((Argon2Hasher(),))

TOKEN_TYPE_ACCESS = "access"  # noqa: S105


def hash_password(plain_password: str) -> str:
    return _password_hash.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return _password_hash.verify(plain_password, hashed_password)
    except Exception:  # corrupted hash or unknown scheme
        return False


@dataclass(frozen=True, slots=True)
class AccessTokenPayload:
    subject: str
    platform_role: str | None
    token_id: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(subject: str, platform_role: str | None) -> AccessTokenPayload:
    issued_at = datetime.now(UTC)
    return AccessTokenPayload(
        subject=subject,
        platform_role=platform_role,
        token_id=new_id(),
        issued_at=issued_at,
        expires_at=issued_at + timedelta(minutes=settings.access_token_ttl_minutes),
    )


def encode_access_token(payload: AccessTokenPayload) -> str:
    claims: dict[str, Any] = {
        "sub": payload.subject,
        "pr": payload.platform_role,
        "jti": payload.token_id,
        "typ": TOKEN_TYPE_ACCESS,
        "iat": payload.issued_at,
        "exp": payload.expires_at,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(
        claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token expirado.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token inválido.") from exc
    if claims.get("typ") != TOKEN_TYPE_ACCESS:
        raise AuthenticationError("Tipo de token inesperado.")
    return claims


def generate_opaque_token(nbytes: int = 48) -> tuple[str, str]:
    """High-entropy opaque token. Only the SHA-256 goes to the database."""
    raw = secrets.token_urlsafe(nbytes)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tokens_match(raw: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(raw), stored_hash)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
