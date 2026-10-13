"""Entrega pela Uber com a conta da loja: chaves, ambiente e o que falta (docs/15).

Igual às chaves da transportadora: só o dono mexe (`shipping:config`, de membros), as chaves são
conferidas na Uber **antes** de guardar, ficam cifradas no cofre da loja e só voltam mascaradas.
O resto da configuração (coleta, teto, chamada automática, quem entrega cada zona) é da tela de
Entrega e vai pelas configurações da loja.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentAdmin, DbSession, admin_actor, require_tenant_scopes
from app.audit.writer import audit
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.core.rate_limit import rate_limit
from app.core.scopes import Scope
from app.courier import uber
from app.courier.models import CourierDelivery, CourierStatus
from app.courier.service import CourierService, get_order
from app.integrations.credentials import CredentialStore
from app.models.base import utcnow
from app.orders.models import Order
from app.orders.state_machine import OrderStatus
from app.schemas.common import StrictModel
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor, TenantService
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
        # Só as três chaves da loja; o token guardado não é coisa que a tela mostre.
        masked={c: v[0] for c, v in guardado.items() if c in uber.SECRET_KEYS},
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


# ------------------------------------------------------------------ a corrida do pedido

orders_router = APIRouter(
    prefix="/admin/tenants/{tenant_id}/orders/{order_id}/courier", tags=["Painel — Envio"]
)

#: Chamar a Uber gasta na conta da loja, como comprar etiqueta: é de quem movimenta o pedido.
CourierOperator = Annotated[TenantContext, Depends(require_tenant_scopes(Scope.ORDERS_TRANSITION))]
OrderId = Annotated[str, Path(min_length=36, max_length=36)]


class CourierDeliveryRead(BaseModel):
    id: str
    status: str
    provider_status: str | None
    environment: str
    fee_cents: int | None
    quoted_fee_cents: int | None
    max_fee_cents: int | None
    tracking_url: str | None
    courier_name: str | None
    courier_vehicle: str | None
    pickup_eta: datetime | None
    dropoff_eta: datetime | None
    last_error: str | None
    attempts: int
    requested_by_actor: str
    created_at: datetime
    dispatched_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    closed_at: datetime | None


class CourierRead(BaseModel):
    open: CourierDeliveryRead | None
    history: list[CourierDeliveryRead]
    #: O que impede chamar agora: `pronto` (o pedido não está Pronto), `chaves`, `coleta`.
    missing: list[str]
    #: A zona do pedido é da Uber (o "Pronto" chama sozinho quando a loja deixou).
    uber_zone: bool


class CourierCancelIn(StrictModel):
    #: "Levar eu mesmo": cancela a corrida e a equipe da loja leva.
    own: bool = False


def _row_read(row: CourierDelivery) -> CourierDeliveryRead:
    return CourierDeliveryRead.model_validate(row, from_attributes=True)


async def _courier_read(session: AsyncSession, tenant_id: str, order: Order) -> CourierRead:
    service = CourierService(session, tenant_id, Actor.system("panel"))
    historico = await service.history(order.id)
    aberta = next((r for r in historico if r.active_marker == 1), None)
    cfg = await service.delivery_settings()
    guardado = await CredentialStore(session, tenant_id).status(uber.PROVIDER)
    faltando = [
        nome
        for nome, ok in (
            ("pronto", order.status == OrderStatus.READY_FOR_PICKUP),
            ("chaves", all(c in guardado for c in uber.SECRET_KEYS)),
            ("coleta", cfg.origin is not None),
        )
        if not ok
    ]
    return CourierRead(
        open=_row_read(aberta) if aberta is not None else None,
        history=[_row_read(r) for r in historico if r is not aberta],
        missing=faltando,
        uber_zone=(order.fulfillment or {}).get("courier") == "uber",
    )


async def _delivery_order(session: AsyncSession, order_id: str, *, lock: bool = False) -> Order:
    order = await get_order(session, order_id, lock=lock)
    if order.fulfillment_type != "delivery":
        raise NotFoundError("Este pedido não é de entrega.")
    return order


@orders_router.get("", response_model=CourierRead, summary="A corrida da Uber do pedido")
async def read_courier(
    session: DbSession, user: CurrentAdmin, tenant: CourierOperator, order_id: OrderId
) -> CourierRead:
    return await _courier_read(session, tenant.id, await _delivery_order(session, order_id))


@orders_router.post(
    "/request",
    response_model=CourierRead,
    summary="Chama a Uber para o pedido pronto (ou chama de novo)",
    dependencies=[Depends(rate_limit("courier_request", 10, 60))],
)
async def request_courier(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CourierOperator,
    order_id: OrderId,
) -> CourierRead:
    """Reserva a corrida e já chama: o operador vê o resultado na hora. Se a Uber não
    responder, a corrida fica para o varredor tentar de novo."""
    service = CourierService(session, tenant.id, admin_actor(request, user))
    order = await _delivery_order(session, order_id, lock=True)
    row = await service.reserve(order, auto=False)
    assert row is not None
    await session.commit()
    await service.process(row.id)
    return await _courier_read(session, tenant.id, await _delivery_order(session, order_id))


@orders_router.post(
    "/confirm",
    response_model=CourierRead,
    summary='"Chamar mesmo assim": aceita a cotação acima do teto',
    dependencies=[Depends(rate_limit("courier_request", 10, 60))],
)
async def confirm_courier(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CourierOperator,
    order_id: OrderId,
) -> CourierRead:
    service = CourierService(session, tenant.id, admin_actor(request, user))
    order = await _delivery_order(session, order_id, lock=True)
    row = await service.confirm_fee(order)
    await session.commit()
    await service.process(row.id)
    return await _courier_read(session, tenant.id, await _delivery_order(session, order_id))


@orders_router.post(
    "/cancel",
    response_model=CourierRead,
    summary="Cancela a corrida da Uber (ou: a loja leva)",
    dependencies=[Depends(rate_limit("courier_request", 10, 60))],
)
async def cancel_courier(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CourierOperator,
    order_id: OrderId,
    body: CourierCancelIn,
) -> CourierRead:
    service = CourierService(session, tenant.id, admin_actor(request, user))
    await _delivery_order(session, order_id, lock=True)
    motivo = "a loja vai levar" if body.own else "cancelada pela loja"
    row = await service.request_cancel(order_id, reason=motivo)
    if row is None:
        raise ConflictError("Não há corrida aberta neste pedido.")
    await session.commit()
    if row.status == CourierStatus.CANCELLING:
        await service.process(row.id)
    return await _courier_read(session, tenant.id, await _delivery_order(session, order_id))


@orders_router.post(
    "/refresh",
    response_model=CourierRead,
    summary="Pergunta à Uber como está a corrida",
    dependencies=[Depends(rate_limit("courier_refresh", 30, 60))],
)
async def refresh_courier(
    request: Request,
    session: DbSession,
    user: CurrentAdmin,
    tenant: CourierOperator,
    order_id: OrderId,
) -> CourierRead:
    await _delivery_order(session, order_id)
    await CourierService(session, tenant.id, admin_actor(request, user)).refresh(order_id)
    return await _courier_read(session, tenant.id, await _delivery_order(session, order_id))
