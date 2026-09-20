from __future__ import annotations

import secrets

from uuid6 import uuid7


def new_id() -> str:
    """UUIDv7 as canonical string: time-ordered, so InnoDB clustering stays sane."""
    return str(uuid7())


def new_public_key(length: int = 32) -> str:
    """Opaque URL-safe identifier (tenant_key in webhook paths, tokens)."""
    return secrets.token_urlsafe(length)[:length]
