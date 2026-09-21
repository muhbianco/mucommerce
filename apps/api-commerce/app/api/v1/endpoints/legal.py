"""Store legal documents: published in the panel, read by the storefront."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import (
    CurrentAdmin,
    DbSession,
    StorefrontTenant,
    admin_actor,
    require_tenant_scopes,
)
from app.core.exceptions import NotFoundError
from app.core.scopes import Scope
from app.customers.legal import LegalService
from app.customers.legal_models import LegalDocument
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext

Kind = Literal["terms", "privacy"]
MAX_CONTENT = 100_000

router = APIRouter(prefix="/admin/tenants/{tenant_id}/legal-documents", tags=["Painel do tenant"])
storefront_router = APIRouter(prefix="/storefront", tags=["Vitrine (público)"])

SettingsWriter = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))]


class DocumentSummary(BaseModel):
    kind: str
    version: int
    published_at: datetime


class DocumentRead(DocumentSummary):
    content: str
    sha256: str
    created_by_actor: str | None = None


class LegalOverview(BaseModel):
    terms: DocumentRead | None
    privacy: DocumentRead | None
    history: list[DocumentSummary]


class PublishBody(StrictModel):
    kind: Kind
    content: Annotated[str, Field(min_length=20, max_length=MAX_CONTENT)]


class Policies(BaseModel):
    terms: DocumentSummary | None
    privacy: DocumentSummary | None


def summary(doc: LegalDocument) -> DocumentSummary:
    return DocumentSummary(kind=doc.kind, version=doc.version, published_at=doc.published_at)


def read(doc: LegalDocument, *, with_author: bool = False) -> DocumentRead:
    return DocumentRead(
        kind=doc.kind,
        version=doc.version,
        published_at=doc.published_at,
        content=doc.content,
        sha256=doc.sha256,
        created_by_actor=doc.created_by_actor if with_author else None,
    )


# ------------------------------------------------------------------ panel
@router.get("", response_model=LegalOverview, summary="Termos e privacidade da loja")
async def legal_overview(session: DbSession, tenant: SettingsWriter) -> LegalOverview:
    service = LegalService(session, tenant)
    latest = await service.latest_versions()
    history = [summary(d) for kind in ("terms", "privacy") for d in await service.history(kind)]
    return LegalOverview(
        terms=read(latest["terms"], with_author=True) if "terms" in latest else None,
        privacy=read(latest["privacy"], with_author=True) if "privacy" in latest else None,
        history=history,
    )


@router.post(
    "",
    response_model=DocumentRead,
    status_code=201,
    summary="Publica uma nova versão (as anteriores ficam guardadas para os aceites)",
)
async def publish_document(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: SettingsWriter,
    body: PublishBody,
) -> DocumentRead:
    doc = await LegalService(session, tenant).publish(
        body.kind, body.content, admin_actor(request, user)
    )
    return read(doc, with_author=True)


# ------------------------------------------------------------------ storefront
def _storefront_open(tenant: TenantContext) -> None:
    if not tenant.feature("storefront"):
        raise NotFoundError("Recurso não encontrado.")


@storefront_router.get("/policies", response_model=Policies, summary="Versões vigentes")
async def policies(session: DbSession, tenant: StorefrontTenant) -> Policies:
    _storefront_open(tenant)
    latest = await LegalService(session, tenant).latest_versions()
    return Policies(
        terms=summary(latest["terms"]) if "terms" in latest else None,
        privacy=summary(latest["privacy"]) if "privacy" in latest else None,
    )


@storefront_router.get(
    "/policies/{kind}", response_model=DocumentRead, summary="Texto vigente de um documento"
)
async def policy(session: DbSession, tenant: StorefrontTenant, kind: Kind) -> DocumentRead:
    _storefront_open(tenant)
    doc = await LegalService(session, tenant).latest(kind)
    if doc is None:
        raise NotFoundError("Documento não publicado.")
    return read(doc)
