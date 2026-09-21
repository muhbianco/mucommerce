"""Customer sessions: opaque random token in the `__Host-mb_sess` cookie, SHA-256 in the table.

30 days, sliding (renewed at most once a day of use), one row per sign-in, revocable. A session
belongs to one store: the lookup runs with the session bound to the store resolved from the
Host, so a token of store A never works on store B.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.customers.access import Viewer
from app.customers.repository import AccessRepository, CustomerSessionRepository
from app.identity.models import CustomerSession

SESSION_COOKIE = "__Host-mb_sess"
SESSION_TTL = timedelta(days=30)
SLIDE_EVERY = timedelta(days=1)
# Tokens are 43 url-safe characters; anything far outside that is not ours.
MAX_TOKEN_LENGTH = 128


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def resolve_viewer(
    session: AsyncSession, tenant_id: str, token: str, now: datetime
) -> Viewer | None:
    """The signed-in customer for `token` in this store, or None (unknown, revoked, expired)."""
    if not token or len(token) > MAX_TOKEN_LENGTH:
        return None
    row = await CustomerSessionRepository(session).by_token_hash(hash_token(token))
    if row is None or row.tenant_id != tenant_id or row.revoked_at is not None:
        return None
    if row.expires_at <= now:
        return None
    if row.expires_at - now < SESSION_TTL - SLIDE_EVERY:
        row.expires_at = now + SESSION_TTL
    access = await AccessRepository(session).for_customer(row.customer_id)
    return Viewer(
        customer_id=row.customer_id,
        session_id=row.id,
        access_status=access.status if access else None,
    )


def open_session(
    session: AsyncSession,
    *,
    tenant_id: str,
    customer_id: str,
    now: datetime,
    ip: str | None,
    user_agent: str | None,
) -> tuple[str, CustomerSession]:
    """Add a new session row; returns the raw token (shown once, to set the cookie)."""
    token = new_token()
    row = CustomerSession(
        tenant_id=tenant_id,
        customer_id=customer_id,
        token_hash=hash_token(token),
        expires_at=now + SESSION_TTL,
        ip=ip,
        user_agent=(user_agent or "")[:300] or None,
    )
    session.add(row)
    return token, row
