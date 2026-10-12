"""Painel: os módulos que o próprio lojista liga e quem vê a vitrine.

Os módulos que não são dele aparecem aqui, mas travados e com o motivo: ligar um pago é compra,
e compra acontece na loja de serviços do site, onde existe carteira, termos e renovação; os da
MuhBianco ela liga pelo admin. Esconder o módulo só faria o lojista achar que a loja não tem
aquilo. O admin do site lê esta mesma lista e usa `owner` para saber o que é dele.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.core.exceptions import ValidationError
from app.core.scopes import Scope
from app.customers.store_pass import STORE_PASSWORD_KEY, STORE_PASSWORD_PROVIDER
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
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
    #: Quem manda no módulo: `store` (painel), `subscription` (compra) ou `platform` (admin).
    owner: modules.Owner
    #: `owner == "store"`; fica para quem ainda lê o campo antigo.
    self_service: bool
    #: Ligado sempre; o painel mostra sem botão.
    always_on: bool
    locked_reason: str | None
    requires: list[str]
    enabled: bool
    #: Ligados que dependem deste — desligá-lo quebraria estes.
    dependents: list[str]


class ModulesUpdate(BaseModel):
    flags: Annotated[dict[str, bool], Field(min_length=1, max_length=40)]


class AccessModeUpdate(BaseModel):
    access_mode: AccessMode
    #: Loja fechada: nem a página inicial aparece antes de entrar. Omitido = mantém como está.
    landing_locked: bool | None = None


class AccessRead(BaseModel):
    access_mode: str
    landing_locked: bool
    password_set: bool


class StorePasswordIn(BaseModel):
    #: É senha de porta, que o lojista passa a quem quiser: curta e digitável no celular.
    password: Annotated[str, Field(min_length=4, max_length=64)]


class StorePasswordRead(BaseModel):
    set: bool
    masked: str | None = None
    changed_at: datetime | None = None
    #: Só com `?ver=1`: o lojista copia para mandar a quem quiser.
    password: str | None = None


def _view(enabled: dict[str, bool]) -> list[ModuleRead]:
    """O que esta loja vê. Módulo por convite desligado não entra na lista.

    Não é enfeite de tela: um cartão "em breve" conta a quem está de fora que o produto existe,
    e há quem não deva saber disso por esta porta.
    """
    return [
        ModuleRead(
            key=module.key,
            label=module.label,
            summary=module.summary,
            where=module.where,
            owner=module.owner,
            self_service=module.self_service,
            always_on=module.always_on,
            locked_reason=module.locked_reason,
            requires=list(module.requires),
            enabled=enabled.get(module.key, False),
            dependents=[d for d in modules.dependents(module.key) if enabled.get(d, False)],
        )
        for module in modules.MODULES
        if not module.invite_only or enabled.get(module.key, False)
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


async def _storefront_now(service: TenantService, tenant_id: str) -> dict[str, Any]:
    """A configuração da vitrine lida do banco, não do contexto: o contexto vem do cache de 60 s,
    e regravar a partir dele poderia devolver a versão de uma senha já trocada, reabrindo a loja
    para quem o lojista acabou de trancar."""
    return dict((await service.repo.settings(tenant_id)).get("storefront") or {})


def _access_read(storefront: dict[str, Any]) -> AccessRead:
    return AccessRead(
        access_mode=str(storefront.get("access_mode", "whitelist")),
        landing_locked=bool(storefront.get("landing_locked")),
        password_set=bool(storefront.get("password_set")),
    )


@router.get("/storefront/access", response_model=AccessRead, summary="Quem vê a vitrine da loja")
async def read_access_mode(session: DbSession, user: CurrentAdmin, tenant: ModulesReader) -> Any:
    return _access_read(await _storefront_now(TenantService(session), tenant.id))


@router.put("/storefront/access", response_model=AccessRead, summary="Quem vê a vitrine da loja")
async def update_access_mode(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: ModulesWriter,
    body: AccessModeUpdate,
) -> Any:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    novo = await _storefront_now(service, tenant.id) | {"access_mode": body.access_mode}
    if body.landing_locked is not None:
        novo["landing_locked"] = body.landing_locked
    await service.set_setting(row, "storefront", novo, admin_actor(request, user))
    return _access_read(novo)


@router.get(
    "/storefront/password", response_model=StorePasswordRead, summary="A senha da loja fechada"
)
async def read_store_password(
    session: DbSession,
    user: CurrentAdmin,
    tenant: ModulesWriter,
    ver: Annotated[bool, Query()] = False,
) -> Any:
    store = CredentialStore(session, tenant.id)
    status = (await store.status(STORE_PASSWORD_PROVIDER)).get(STORE_PASSWORD_KEY)
    if status is None:
        return StorePasswordRead(set=False)
    return StorePasswordRead(
        set=True,
        masked=status[0],
        changed_at=status[1],
        password=await store.get(STORE_PASSWORD_PROVIDER, STORE_PASSWORD_KEY) if ver else None,
    )


@router.put(
    "/storefront/password", response_model=StorePasswordRead, summary="Define ou troca a senha"
)
async def set_store_password(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: ModulesWriter,
    body: StorePasswordIn,
) -> Any:
    senha = body.password.strip()
    if len(senha) < 4:
        raise ValidationError("A senha precisa de pelo menos 4 caracteres.", reason="too_short")
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    masked = await CredentialStore(session, tenant.id).put(
        STORE_PASSWORD_PROVIDER, STORE_PASSWORD_KEY, senha
    )
    await _bump_password(service, row, tenant.id, admin_actor(request, user), enabled=True)
    return StorePasswordRead(set=True, masked=masked, changed_at=utcnow())


@router.delete("/storefront/password", status_code=204, summary="Tira a senha da loja")
async def delete_store_password(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: ModulesWriter
) -> Response:
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    await CredentialStore(session, tenant.id).delete(STORE_PASSWORD_PROVIDER, STORE_PASSWORD_KEY)
    await _bump_password(service, row, tenant.id, admin_actor(request, user), enabled=False)
    return Response(status_code=204)


async def _bump_password(
    service: TenantService, row: Any, tenant_id: str, actor: Any, *, enabled: bool
) -> None:
    """Toda troca (ou retirada) sobe a versão: os passes emitidos com a senha anterior param de
    valer na hora. A senha em si nunca passa por aqui nem pelo audit, só o fato da troca."""
    atual = await _storefront_now(service, tenant_id)
    versao = int(atual.get("password_version") or 0) + 1
    await service.set_setting(
        row,
        "storefront",
        atual | {"password_set": enabled, "password_version": versao},
        actor,
    )
