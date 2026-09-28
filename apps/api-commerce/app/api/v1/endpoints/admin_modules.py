"""Painel: os módulos que o próprio lojista liga e quem vê a vitrine.

Os módulos pagos aparecem aqui, mas travados e com o motivo: ligar um deles é compra, e compra
acontece na loja de serviços do site, onde existe carteira, termos e renovação. Esconder o
módulo só faria o lojista achar que a loja não tem aquilo.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.scopes import Scope
from app.tenancy import modules
from app.tenancy.context import TenantContext
from app.tenancy.models import DEFAULT_FEATURE_FLAGS
from app.tenancy.service import TenantService
from app.tenancy.settings_schemas import AccessMode

router = APIRouter(prefix="/admin/tenants/{tenant_id}", tags=["Painel — Módulos"])

# Ligar módulo é decisão de quem paga, então escrever é só para a equipe da loja. **Ler não**:
# o suporte da MuhBianco precisa enxergar o que está ligado para responder "por que minha loja
# não vende". Sem esta separação, a tela inteira morre para quem só queria olhar.
ModulesReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE))]
ModulesWriter = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.SETTINGS_WRITE, members_only=True))
]


class ModuleRead(BaseModel):
    key: str
    label: str
    summary: str
    where: str | None
    self_service: bool
    locked_reason: str | None
    requires: list[str]
    enabled: bool
    #: Ligados que dependem deste — desligá-lo quebraria estes.
    dependents: list[str]


class ModulesUpdate(BaseModel):
    flags: Annotated[dict[str, bool], Field(min_length=1, max_length=40)]


class AccessModeUpdate(BaseModel):
    access_mode: AccessMode


def _view(enabled: dict[str, bool]) -> list[ModuleRead]:
    return [
        ModuleRead(
            key=module.key,
            label=module.label,
            summary=module.summary,
            where=module.where,
            self_service=module.self_service,
            locked_reason=module.locked_reason,
            requires=list(module.requires),
            enabled=enabled.get(module.key, False),
            dependents=[d for d in modules.dependents(module.key) if enabled.get(d, False)],
        )
        for module in modules.MODULES
    ]


async def _enabled(session: DbSession, tenant_id: str) -> dict[str, bool]:
    stored = await TenantService(session).repo.feature_flags(tenant_id)
    # Flag criada depois da loja não tem linha ainda: vale como desligada, mas aparece na tela.
    return {key: stored.get(key, False) for key in DEFAULT_FEATURE_FLAGS}


@router.get("/modules", response_model=list[ModuleRead], summary="Módulos da loja")
async def list_modules(session: DbSession, user: CurrentAdmin, tenant: ModulesReader) -> Any:
    return _view(await _enabled(session, tenant.id))


@router.put("/modules", response_model=list[ModuleRead], summary="Liga ou desliga módulos")
async def update_modules(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: ModulesWriter,
    body: ModulesUpdate,
) -> Any:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    await service.set_self_service_features(row, body.flags, admin_actor(request, user))
    return _view(await _enabled(session, tenant.id))


@router.put(
    "/storefront/access", response_model=dict[str, str], summary="Quem vê a vitrine da loja"
)
async def update_access_mode(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: ModulesWriter,
    body: AccessModeUpdate,
) -> dict[str, str]:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    atual = dict(tenant.settings.get("storefront") or {})
    await service.set_setting(
        row, "storefront", atual | {"access_mode": body.access_mode}, admin_actor(request, user)
    )
    return {"access_mode": body.access_mode}
