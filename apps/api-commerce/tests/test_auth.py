from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.scopes import PlatformRole
from tests.conftest import TEST_PASSWORD, create_admin


async def test_login_wrong_password_and_unknown_user_look_the_same(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_admin(session_factory, "ops@muhbianco.test", platform_role=PlatformRole.OPERATOR)
    wrong = await client.post(
        "/api/v1/auth/token", data={"username": "ops@muhbianco.test", "password": "nope-nope-nope"}
    )
    unknown = await client.post(
        "/api/v1/auth/token", data={"username": "ghost@muhbianco.test", "password": TEST_PASSWORD}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"] == unknown.json()["error"] | {
        "request_id": wrong.json()["error"]["request_id"]
    }


async def test_refresh_rotation_and_reuse_detection(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await create_admin(session_factory, "ops@muhbianco.test", platform_role=PlatformRole.OPERATOR)
    login = await client.post(
        "/api/v1/auth/token", data={"username": "ops@muhbianco.test", "password": TEST_PASSWORD}
    )
    first = login.json()

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Replaying the old token revokes the whole family.
    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]}
    )
    assert replay.status_code == 401

    dead = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert dead.status_code == 401

    # Access token itself is still valid until it expires.
    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {second['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["platform_role"] == "operator"


async def test_logout_revokes_refresh(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    login = await client.post(
        "/api/v1/auth/token", data={"username": "ops@muhbianco.test", "password": TEST_PASSWORD}
    )
    tokens = login.json()
    out = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert out.status_code == 204
    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_operator_can_list_tenants_superadmin_only_routes_exist(
    client: AsyncClient, operator_headers: dict[str, str]
) -> None:
    response = await client.get("/api/v1/ops/tenants", headers=operator_headers)
    assert response.status_code == 200
    assert response.json() == []
