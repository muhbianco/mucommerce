from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Input models reject unknown fields: a typo never silently becomes a no-op."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable code.")
    message: str = Field(description="Human-readable message (pt-BR).")
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Single error envelope: `{"error": {...}}`."""

    error: ErrorBody


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadinessResponse(HealthResponse):
    database: bool
    redis: bool | None = None
    # Reported, not gating: the catalog keeps working when only uploads are down.
    storage: bool | None = None
