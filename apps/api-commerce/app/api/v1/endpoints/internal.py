from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api.deps import DbSession, StorefrontTenant, require_internal
from app.schemas.internal import StorefrontContext
from app.tenancy.edge import build_traefik_config
from app.tenancy.repository import TenantRepository
from app.tenancy.storefront_context import build_storefront_context

router = APIRouter(prefix="/internal", tags=["Interno"])


@router.get(
    "/edge/traefik",
    summary="Configuração dinâmica do Traefik (providers.http)",
    dependencies=[Depends(require_internal("traefik"))],
    response_model=None,
)
async def traefik_dynamic_config(request: Request, session: DbSession) -> Response:
    """Routers/services/middlewares for every active tenant host. ETag for cheap polling."""
    domains = await TenantRepository(session).list_active_storefront_domains()
    config: dict[str, Any] = build_traefik_config(domains)
    body = json.dumps(config, sort_keys=True, separators=(",", ":"))
    etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(
        content=config,
        headers={"ETag": etag, "Cache-Control": "max-age=15"},
    )


@router.get(
    "/storefront/context",
    response_model=StorefrontContext,
    summary="Contexto do tenant para o Next.js (X-Tenant-Host + token interno)",
    dependencies=[Depends(require_internal("web"))],
)
async def storefront_context(session: DbSession, tenant: StorefrontTenant) -> StorefrontContext:
    return await build_storefront_context(session, tenant, internal=True)
