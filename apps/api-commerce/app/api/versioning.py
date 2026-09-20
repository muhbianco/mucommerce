from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import APIRouter, FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import NoIndexMiddleware
from app.core.config import settings


@dataclass(frozen=True, slots=True)
class ApiVersion:
    name: str
    router: APIRouter
    summary: str
    deprecated: bool = False
    sunset: str | None = None
    tags_metadata: list[dict[str, object]] = field(default_factory=list)

    @property
    def prefix(self) -> str:
        return f"/api/{self.name}"


def _build_description(version: ApiVersion) -> str:
    lines = [settings.api_description, "", version.summary]
    if version.deprecated:
        notice = "Esta versão está **descontinuada**."
        if version.sunset:
            notice += f" Desligamento previsto para **{version.sunset}**."
        lines += ["", notice]
    lines += [
        "",
        "### Autenticação",
        "",
        "- Painel/ops: `POST auth/token` (e-mail e senha) e `Authorization: Bearer <token>`.",
        "- Serviços internos (Next.js, Traefik, api-agents): header `X-Internal-Token`.",
        "- Storefront: cookie de sessão emitido após o login Google (fase 1).",
        "",
        "O tenant nunca é informado pelo cliente: vem do `Host`, do `X-Tenant-Host` "
        "(com token interno) ou do caminho validado por membership.",
    ]
    return "\n".join(lines)


def create_version_app(version: ApiVersion) -> FastAPI:
    docs_enabled = settings.docs_enabled
    version_app = FastAPI(
        title=f"{settings.api_title} — {version.name}",
        description=_build_description(version),
        version=__version__,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        openapi_tags=version.tags_metadata or None,
        swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
        contact={"name": "MuhBianco", "url": "https://muhbianco.com.br"},
    )
    version_app.add_middleware(NoIndexMiddleware)
    register_exception_handlers(version_app)
    version_app.include_router(version.router)
    return version_app
