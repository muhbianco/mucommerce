from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.scopes import Scope
from app.core.storage import ObjectStorage, get_storage
from app.media.models import MediaAsset
from app.media.schemas import (
    MediaOwnerIn,
    MediaRead,
    MediaUpdate,
    RenditionRead,
    UploadCreate,
    UploadCreated,
    UploadForm,
)
from app.media.service import MediaService, rendition_urls
from app.tenancy.context import TenantContext

router = APIRouter(prefix="/admin/tenants/{tenant_id}/media", tags=["Painel — Mídia"])

MediaReadTenant = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.CATALOG_READ))]
MediaWriteTenant = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.MEDIA_WRITE))]
Storage = Annotated[ObjectStorage, Depends(get_storage)]
MediaId = Annotated[str, Path(min_length=36, max_length=36)]


def media_read(media: MediaAsset) -> MediaRead:
    return MediaRead(
        id=media.id,
        owner_type=media.owner_type,
        owner_id=media.owner_id,
        status=media.status,
        alt=media.alt,
        position=media.position,
        width=media.width,
        height=media.height,
        failure_reason=media.failure_reason,
        renditions=[RenditionRead(**item) for item in rendition_urls(media)],
        created_at=media.created_at,
    )


@router.post(
    "/uploads",
    response_model=UploadCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Reserva uma imagem e devolve o formulário de upload direto para o storage",
)
async def create_upload(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: MediaWriteTenant,
    storage: Storage,
    body: UploadCreate,
) -> UploadCreated:
    service = MediaService(session, tenant, admin_actor(request, user), storage)
    ticket = await service.create_upload(body)
    return UploadCreated(
        media=media_read(ticket.media),
        upload=UploadForm(
            url=ticket.form.url, fields=ticket.form.fields, expires_at=ticket.expires_at
        ),
    )


@router.post(
    "/{media_id}/complete",
    response_model=MediaRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Confirma o upload; o processamento (WebP) roda em segundo plano",
)
async def complete_upload(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: MediaWriteTenant,
    storage: Storage,
    media_id: MediaId,
) -> MediaRead:
    service = MediaService(session, tenant, admin_actor(request, user), storage)
    return media_read(await service.complete(media_id))


@router.get("", response_model=list[MediaRead], summary="Imagens de um dono (produto, marca…)")
async def list_media(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: MediaReadTenant,
    owner_type: MediaOwnerIn,
    owner_id: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
) -> list[MediaRead]:
    service = MediaService(session, tenant, admin_actor(request, user))
    return [media_read(m) for m in await service.list_for_owner(owner_type, owner_id)]


@router.get("/{media_id}", response_model=MediaRead, summary="Estado de uma imagem (polling)")
async def get_media(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: MediaReadTenant,
    media_id: MediaId,
) -> MediaRead:
    service = MediaService(session, tenant, admin_actor(request, user))
    return media_read(await service.get(media_id))


@router.patch("/{media_id}", response_model=MediaRead, summary="Texto alternativo e posição")
async def update_media(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: MediaWriteTenant,
    media_id: MediaId,
    body: MediaUpdate,
) -> MediaRead:
    service = MediaService(session, tenant, admin_actor(request, user))
    return media_read(await service.update(media_id, body))


@router.delete(
    "/{media_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a imagem (os arquivos são apagados em segundo plano)",
)
async def delete_media(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: MediaWriteTenant,
    media_id: MediaId,
) -> Response:
    service = MediaService(session, tenant, admin_actor(request, user))
    await service.delete(media_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
