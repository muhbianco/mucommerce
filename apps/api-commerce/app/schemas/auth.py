from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import StrictModel


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=200)


class LogoutRequest(StrictModel):
    refresh_token: str | None = Field(default=None, min_length=20, max_length=200)


class MembershipRead(BaseModel):
    tenant_id: str
    tenant_slug: str
    role: str
    scopes: list[str]


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    platform_role: str | None
    memberships: list[MembershipRead]


class SsoExchangeRequest(StrictModel):
    """One-time code from the MuhBianco login plus the PKCE verifier (RFC 7636)."""

    code: str = Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code_verifier: str = Field(min_length=43, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$")
    # panel: full session (the panel keeps it in HttpOnly cookies).
    # site_admin: a short access token only, for the "Lojas" page of the MuhBianco admin.
    purpose: Literal["panel", "site_admin"] = "panel"


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int
