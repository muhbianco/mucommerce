from __future__ import annotations

import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import idempotency
from app.audit.models import IdempotencyKey
from app.identity.models import AdminRefreshToken
from app.identity.repository import AdminUserRepository
from app.models.base import utcnow

TTL_LEFT = timedelta(hours=1)


async def _expire_all_keys(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        await session.execute(
            update(IdempotencyKey).values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()


async def test_expired_key_is_processed_as_a_new_request(
    client: AsyncClient,
    operator_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Regression: expires_at was stored but never checked, so a key blocked forever."""
    headers = {**operator_headers, "Idempotency-Key": str(uuid.uuid4())}
    first = await client.post(
        "/api/v1/ops/tenants", json={"slug": "primeira", "name": "Primeira"}, headers=headers
    )
    assert first.status_code == 201, first.text

    await _expire_all_keys(session_factory)
    second = await client.post(
        "/api/v1/ops/tenants", json={"slug": "segunda", "name": "Segunda"}, headers=headers
    )
    assert second.status_code == 201, second.text
    assert second.headers.get("Idempotent-Replayed") is None
    assert second.json()["id"] != first.json()["id"]

    replay = await client.post(
        "/api/v1/ops/tenants", json={"slug": "segunda", "name": "Segunda"}, headers=headers
    )
    assert replay.headers.get("Idempotent-Replayed") == "true"  # fresh TTL again


async def test_purge_deletes_only_expired_rows_in_batches(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        for n in range(7):
            session.add(
                IdempotencyKey(
                    scope="test",
                    idem_key=f"old-{n}",
                    request_hash="x" * 64,
                    expires_at=now - timedelta(hours=1),
                )
            )
        session.add(
            IdempotencyKey(
                scope="test", idem_key="live", request_hash="x" * 64, expires_at=now + TTL_LEFT
            )
        )
        await session.commit()

    async with session_factory() as session:
        assert await idempotency.purge_expired(session, batch_size=3) == 7
        await session.commit()
        remaining = (await session.execute(select(IdempotencyKey.idem_key))).scalars().all()
    assert remaining == ["live"]


async def test_purge_keeps_refresh_tokens_inside_the_retention_window(
    client: AsyncClient,
    operator_headers: dict[str, str],
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del client, operator_headers  # the fixture logs an operator in: one refresh token exists
    async with session_factory() as session:
        (token,) = (await session.execute(select(AdminRefreshToken))).scalars().all()
        token.expires_at = utcnow() - timedelta(days=3)
        await session.commit()

    async with session_factory() as session:
        repo = AdminUserRepository(session)
        assert await repo.purge_expired_refresh(older_than=timedelta(days=7)) == 0
        assert await repo.purge_expired_refresh(older_than=timedelta(days=1)) == 1
        await session.commit()
        count = (
            await session.execute(select(func.count()).select_from(AdminRefreshToken))
        ).scalar_one()
    assert count == 0
