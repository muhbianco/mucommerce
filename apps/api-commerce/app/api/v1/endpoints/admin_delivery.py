"""Entrega pela Uber com a conta da loja: chaves, ambiente e o que falta (docs/15).

Igual às chaves da transportadora: só o dono mexe (`shipping:config`, de membros), as chaves são
conferidas na Uber **antes** de guardar, ficam cifradas no cofre da loja e só voltam mascaradas.
O resto da configuração (coleta, teto, chamada automática, quem entrega cada zona) é da tela de
Entrega e vai pelas configurações da loja.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.audit.writer import audit
from app.core.exceptions import ValidationError
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope
from app.courier import uber
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext
from app.tenancy.service import TenantService
from app.tenancy.settings_schemas import AccountCheck, fulfillment_settings

router = APIRouter(prefix="/admin/tenants/{tenant_id}/delivery/uber", tags=["Painel — Envio"])

DeliveryOwner = Annotated[
    TenantContext, Depends(require_tenant_scopes(Scope.SHIPPING_CONFIG, members_only=True))
]
#: Ler não gasta nada: o staff da MuhBianco abre para conferir por que a Uber não foi chamada.
DeliveryReader = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.SHIPPING_CONFIG))]

Ambiente = Literal["test", "production"]
Chave = Annotated[str, Field(min_length=4, max_length=200)]


class UberCredentialsIn(StrictModel):
    customer_id: Chave
    client_id: Chave
    client_secret: Chave
    #: De onde as chaves vieram no painel da Uber. A resposta da Uber não diz (docs/15).
    environment: Ambiente = "production"


class UberStatusRead(BaseModel):
    connected: bool
    #: Formas mascaradas das chaves guardadas (`****` + 4 últimos).
    masked: dict[str, str] = {}
    environment: Ambiente
    auto_dispatch: bool
    max_fee_cents: int | None
    origin_ready: bool
    uber_zones: int
    last_test_ok: bool | None = None
    last_test_detail: str | None = None
    last_test_at: str | None = None
    #: O que falta para o "Pronto" chamar a Uber: `chaves`, `coleta`, `zona`.
    missing: list[str] = []


async def _status(session: AsyncSession, tenant_id: str) -> UberStatusRead:
    """Lido do banco: o contexto da requisição tem cache, e logo depois de gravar mentiria."""
    valores = await TenantService(session).repo.settings(tenant_id)
    cfg = fulfillment_settings(valores).delivery
    guardado = await CredentialStore(session, tenant_id).status(uber.PROVIDER)
    conectado = all(chave in guardado for chave in uber.SECRET_KEYS)
    zonas = sum(1 for z in cfg.zones if z.active and z.courier == "uber")
    check = cfg.uber.account_check
    faltando = [
        nome
        for nome, ok in (
            ("chaves", conectado),
            ("coleta", cfg.origin is not None),
            ("zona", zonas > 0),
        )
        if not ok
    ]
    return UberStatusRead(
        connected=conectado,
        masked={chave: valor[0] for chave, valor in guardado.items()},
        environment=cfg.uber.environment,
        auto_dispatch=cfg.uber.auto_dispatch,
        max_fee_cents=cfg.uber.max_fee_cents,
        origin_ready=cfg.origin is not None,
        uber_zones=zonas,
        last_test_ok=check.ok if check else None,
        last_test_detail=check.detail if check else None,
        last_test_at=check.at if check else None,
        missing=faltando,
    )


async def _save_uber(
    request: Request,
    session: AsyncSession,
    user: Any,
    tenant: TenantContext,
    changes: dict[str, Any],
) -> None:
    """Mescla `changes` em `fulfillment.delivery.uber`, lendo do banco (o contexto tem cache)."""
    service = TenantService(session)
    row = await service.get_or_404(tenant.id)
    valor: dict[str, Any] = dict((await service.repo.settings(tenant.id)).get("fulfillment") or {})
    entrega: dict[str, Any] = dict(valor.get("delivery") or {})
    atual: dict[str, Any] = dict(entrega.get("uber") or {})
    entrega["uber"] = {**atual, **changes}
    valor["delivery"] = entrega
    await service.set_setting(
        row, "fulfillment", valor, admin_actor(request, user), server_fields=True
    )


def _check(resultado: uber.UberCheck) -> dict[str, Any]:
    return AccountCheck(
        provider=uber.PROVIDER,
        ok=resultado.ok,
        detail=resultado.detail[:200],
        at=utcnow().isoformat(),
    ).model_dump()


@router.get("", response_model=UberStatusRead, summary="Entrega pela Uber: chaves e o que falta")
async def read_uber(session: DbSession, tenant: DeliveryReader) -> UberStatusRead:
    return await _status(session, tenant.id)


@router.put(
    "/credentials",
    response_model=UberStatusRead,
    summary="Conecta a conta Uber Direct da loja (só o dono)",
    dependencies=[Depends(rate_limit("uber_test", 5, 60))],
)
async def save_uber_credentials(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: DeliveryOwner,
    body: UberCredentialsIn,
) -> UberStatusRead:
    """Confere na Uber antes de guardar: chave recusada não vira "conectada" na tela. Se a Uber
    não respondeu, guarda e diz que não deu para conferir — não é culpa das chaves."""
    chaves = uber.UberCredentials(
        customer_id=body.customer_id.strip(),
        client_id=body.client_id.strip(),
        client_secret=body.client_secret.strip(),
    )
    resultado = await uber.check_credentials(chaves)
    if not resultado.ok and resultado.refused:
        # `uber_refused_client` / `uber_refused_customer`: o painel diz qual chave conferir.
        raise ValidationError(
            f"A Uber recusou as chaves: {resultado.detail}. Confira se copiou as três da aba "
            "Developer do painel da Uber Direct, do mesmo ambiente (teste ou produção).",
            reason=f"uber_refused_{resultado.refused}",
        )
    mascaradas = await uber.save_credentials(session, tenant.id, chaves)
    await _save_uber(
        request,
        session,
        user,
        tenant,
        {"environment": body.environment, "account_check": _check(resultado)},
    )
    await audit(
        session,
        actor=admin_actor(request, user).id,
        action="delivery.uber_credentials_saved",
        entity_type="tenant",
        entity_id=tenant.id,
        tenant_id=tenant.id,
        after={"masked": mascaradas, "environment": body.environment, "checked": resultado.ok},
    )
    await session.commit()
    return await _status(session, tenant.id)


@router.post(
    "/test",
    response_model=UberStatusRead,
    summary="Confere de novo as chaves guardadas (e guarda o resultado)",
    dependencies=[Depends(rate_limit("uber_test", 5, 60))],
)
async def test_uber(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: DeliveryOwner
) -> UberStatusRead:
    chaves = await uber.load_credentials(session, tenant.id)
    if chaves is None:
        raise ValidationError(
            "Conecte a conta da Uber antes de testar.", reason="uber_not_connected"
        )
    resultado = await uber.check_credentials(chaves)
    await _save_uber(request, session, user, tenant, {"account_check": _check(resultado)})
    await session.commit()
    return await _status(session, tenant.id)


@router.delete(
    "/credentials", response_model=UberStatusRead, summary="Desconecta a conta da Uber (só o dono)"
)
async def forget_uber_credentials(
    request: Request, session: DbSession, user: CurrentAdmin, tenant: DeliveryOwner
) -> UberStatusRead:
    await uber.forget_credentials(session, tenant.id)
    await _save_uber(request, session, user, tenant, {"account_check": None})
    await audit(
        session,
        actor=admin_actor(request, user).id,
        action="delivery.uber_credentials_removed",
        entity_type="tenant",
        entity_id=tenant.id,
        tenant_id=tenant.id,
    )
    await session.commit()
    return await _status(session, tenant.id)
