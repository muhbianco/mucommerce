from __future__ import annotations

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
