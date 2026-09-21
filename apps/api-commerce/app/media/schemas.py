from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.media.imaging import MAX_UPLOAD_BYTES
from app.schemas.common import StrictModel

EntityId = Annotated[str, StringConstraints(min_length=36, max_length=36)]
Position = Annotated[int, Field(ge=-1_000_000, le=1_000_000)]
MediaOwnerIn = Literal["product", "tenant_brand", "landing"]


class UploadCreate(StrictModel):
    owner_type: MediaOwnerIn
    # Required for `product`; tenant-level owners (brand, landing) have none.
    owner_id: EntityId | None = None
    mime: Literal["image/jpeg", "image/png", "image/webp"]
    # Exact file size; the upload policy refuses anything bigger.
    bytes: Annotated[int, Field(ge=1, le=MAX_UPLOAD_BYTES)]
    filename: Annotated[str, Field(max_length=200)] | None = None
    alt: Annotated[str, Field(max_length=300)] | None = None


class MediaUpdate(StrictModel):
    alt: Annotated[str, Field(max_length=300)] | None = None
    position: Position | None = None


class UploadForm(BaseModel):
    """Browser sends `multipart/form-data` to `url` with every field, then the file as `file`."""

    url: str
    fields: dict[str, str]
    expires_at: datetime


class RenditionRead(BaseModel):
    name: str
    url: str
    width: int
    height: int


class MediaRead(BaseModel):
    id: str
    owner_type: str
    owner_id: str | None
    status: str
    alt: str | None
    position: int
    width: int | None
    height: int | None
    failure_reason: str | None
    renditions: list[RenditionRead]
    created_at: datetime


class UploadCreated(BaseModel):
    media: MediaRead
    upload: UploadForm
