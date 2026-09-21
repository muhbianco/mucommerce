from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentAdmin, DbSession
from app.core.config import settings
from app.core.rate_limit import client_ip, rate_limit
from app.core.scopes import scopes_for_tenant_role
from app.identity.service import AdminAuthService
from app.schemas.auth import (
    LogoutRequest,
    MembershipRead,
    MeResponse,
    RefreshRequest,
    TokenResponse,
)
from app.tenancy.repository import TenantRepository

router = APIRouter(prefix="/auth", tags=["Autenticação"])

login_limit = rate_limit(
    "auth.login",
    settings.login_rate_limit_attempts,
    settings.login_rate_limit_window_seconds,
)


@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Login de staff (painel e ops)",
    dependencies=[Depends(login_limit)],
)
async def login(
    request: Request,
    session: DbSession,
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    pair = await AdminAuthService(session).login(
        form.username,
        form.password,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/refresh", response_model=TokenResponse, summary="Rotaciona o refresh token")
async def refresh(request: Request, session: DbSession, body: RefreshRequest) -> TokenResponse:
    pair = await AdminAuthService(session).refresh(
        body.refresh_token,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoga a sessão")
async def logout(session: DbSession, user: CurrentAdmin, body: LogoutRequest | None = None) -> None:
    await AdminAuthService(session).logout(body.refresh_token if body else None, user)


@router.get("/me", response_model=MeResponse, summary="Usuário atual e memberships")
async def me(session: DbSession, user: CurrentAdmin) -> MeResponse:
    active = [m for m in user.memberships if m.status == "active"]
    tenants = await TenantRepository(session).get_many({m.tenant_id for m in active})
    memberships: list[MembershipRead] = []
    for membership in active:
        tenant = tenants.get(membership.tenant_id)
        memberships.append(
            MembershipRead(
                tenant_id=membership.tenant_id,
                tenant_slug=tenant.slug if tenant else "",
                role=membership.role,
                scopes=sorted(str(s) for s in scopes_for_tenant_role(membership.role)),
            )
        )
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        platform_role=user.platform_role,
        memberships=memberships,
    )
