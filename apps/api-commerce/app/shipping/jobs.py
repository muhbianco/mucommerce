"""Rastreio das remessas abertas, no beat (etapa J.4, ADR 0015).

Perguntar à transportadora é caro e o objeto se move devagar: cada remessa é consultada no
máximo a cada `TRACK_EVERY`, em lotes pequenos e sempre em sessão própria — uma transportadora
fora do ar não pode segurar o resto da varredura.

Quando o rastreio diz "entregue", o pedido vai para `delivered` sozinho: ninguém precisa
clicar para confirmar o que os Correios já confirmaram.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.orders.models import Order
from app.orders.service import OrderService
from app.shipping import registry
from app.shipping.models import (
    OPEN_SHIPMENT_STATUSES,
    OrderShipment,
    ShipmentEvent,
    ShipmentStatus,
)
from app.shipping.provider import ShippingCredentials, ShippingProviderError, TrackingResult
from app.tenancy.context import CROSS_TENANT_OPTION, TenantContext, bind_session_tenant
from app.tenancy.resolver import TenantResolver
from app.tenancy.service import Actor

logger = get_logger(__name__)
BATCH = 50
TRACK_EVERY = timedelta(hours=4)
ACTOR = Actor.system("shipment-tracking")

#: Rastreio → estado da remessa. O que não mapeia fica como está (não inventa movimento).
_TO_SHIPMENT = {
    "posted": ShipmentStatus.POSTED,
    "in_transit": ShipmentStatus.IN_TRANSIT,
    "out_for_delivery": ShipmentStatus.IN_TRANSIT,
    "delivered": ShipmentStatus.DELIVERED,
    "returned": ShipmentStatus.RETURNED,
    "cancelled": ShipmentStatus.CANCELLED,
}


async def due_shipments(session: AsyncSession, now: datetime) -> list[tuple[str, str]]:
    corte = now - TRACK_EVERY
    stmt = (
        select(OrderShipment.id, OrderShipment.tenant_id)
        .where(OrderShipment.status.in_(OPEN_SHIPMENT_STATUSES))
        .where(or_(OrderShipment.tracked_at.is_(None), OrderShipment.tracked_at <= corte))
        .order_by(OrderShipment.tracked_at.is_(None).desc(), OrderShipment.tracked_at)
        .limit(BATCH)
        .execution_options(**{CROSS_TENANT_OPTION: True})
    )
    return [(sid, tid) for sid, tid in (await session.execute(stmt)).all()]


async def track_one(
    factory: async_sessionmaker[AsyncSession], shipment_id: str, tenant_id: str, now: datetime
) -> str:
    async with factory() as session:
        tenant = await TenantResolver(session).resolve_by_id(tenant_id)
        bind_session_tenant(session, tenant_id)
        remessa = await session.get(OrderShipment, shipment_id)
        if remessa is None or remessa.provider_shipment_id is None:
            return "ignorado"
        provider = registry.get_provider(remessa.provider)
        if provider is None:
            return "ignorado"
        from app.core.config import settings
        from app.integrations.credentials import CredentialStore

        token = await CredentialStore(session, tenant_id).get(remessa.provider, "access_token")
        if not token:
            return "sem-credencial"
        credenciais = ShippingCredentials(
            secrets={"access_token": token},
            public_config={},
            sandbox=settings.environment != "production",
        )
        # Marca a tentativa antes de perguntar: provedor mudo não vira consulta em loop.
        remessa.tracked_at = now
        etiquetas = [
            (n, str(v["provider_shipment_id"]))
            for n, v in enumerate(remessa.parcels or [], start=1)
            if v.get("provider_shipment_id")
        ]
        if len(etiquetas) > 1:
            mudou = await _track_per_volume(
                session, tenant, provider, credenciais, remessa, etiquetas, now
            )
            await session.commit()
            return "mudou" if mudou else "igual"
        try:
            resultado = await provider.track(credenciais, remessa.provider_shipment_id)
        except ShippingProviderError as exc:
            logger.info(
                "Rastreio indisponível",
                extra={"shipment_id": shipment_id, "erro": type(exc).__name__},
            )
            await session.commit()
            return "sem-resposta"
        mudou = await _apply(session, tenant, remessa, resultado, now)
        await session.commit()
        return "mudou" if mudou else "igual"


async def run_track_shipments(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    async with factory() as session:
        devidas = await due_shipments(session, now)
    mudaram = 0
    for shipment_id, tenant_id in devidas:
        try:
            mudaram += await track_one(factory, shipment_id, tenant_id, now) == "mudou"
        except Exception:
            logger.exception("Falha ao rastrear remessa", extra={"shipment_id": shipment_id})
    return mudaram


async def _apply(
    session: AsyncSession,
    tenant: TenantContext,
    remessa: OrderShipment,
    resultado: TrackingResult,
    now: datetime,
) -> bool:
    if resultado.tracking_code and not remessa.tracking_code:
        remessa.tracking_code = resultado.tracking_code
    for evento in resultado.events:
        await _record(session, remessa, evento.status, evento.description, evento.at)
    destino = _TO_SHIPMENT.get(resultado.status)
    if destino is None or destino == remessa.status:
        return False
    remessa.status = destino
    if destino == ShipmentStatus.POSTED and remessa.posted_at is None:
        remessa.posted_at = now
    if destino == ShipmentStatus.DELIVERED:
        remessa.delivered_at = resultado.delivered_at or now
        await _deliver_order(session, tenant, remessa, now)
    return True


#: Ordem de avanço da remessa: com várias etiquetas, a remessa anda junto com a mais atrasada.
_ORDEM = {
    ShipmentStatus.PURCHASED: 0,
    ShipmentStatus.POSTED: 1,
    ShipmentStatus.IN_TRANSIT: 2,
    ShipmentStatus.DELIVERED: 3,
}


def combine_statuses(statuses: list[ShipmentStatus]) -> ShipmentStatus:
    """Estado da remessa com uma etiqueta por volume.

    Entregue só quando **todos** chegaram; devolvido se algum voltou; cancelado se todos foram
    cancelados; senão, o estado do volume mais atrasado.
    """
    if statuses and all(s == ShipmentStatus.DELIVERED for s in statuses):
        return ShipmentStatus.DELIVERED
    if ShipmentStatus.RETURNED in statuses:
        return ShipmentStatus.RETURNED
    if statuses and all(s == ShipmentStatus.CANCELLED for s in statuses):
        return ShipmentStatus.CANCELLED
    andando = [s for s in statuses if s in _ORDEM]
    return min(andando, key=_ORDEM.__getitem__) if andando else ShipmentStatus.PURCHASED


async def _track_per_volume(
    session: AsyncSession,
    tenant: TenantContext,
    provider: Any,
    credentials: ShippingCredentials,
    remessa: OrderShipment,
    etiquetas: list[tuple[int, str]],
    now: datetime,
) -> bool:
    volumes = [dict(v) for v in remessa.parcels or []]
    estados: list[ShipmentStatus] = []
    for n, provider_id in etiquetas:
        atual = ShipmentStatus(volumes[n - 1].get("status") or ShipmentStatus.PURCHASED)
        try:
            resultado = await provider.track(credentials, provider_id)
        except ShippingProviderError as exc:
            logger.info(
                "Rastreio indisponível",
                extra={"shipment_id": remessa.id, "volume": n, "erro": type(exc).__name__},
            )
            estados.append(atual)
            continue
        if resultado.tracking_code and not volumes[n - 1].get("tracking_code"):
            volumes[n - 1]["tracking_code"] = resultado.tracking_code
        for evento in resultado.events:
            await _record(
                session, remessa, evento.status, f"Volume {n}: {evento.description}", evento.at
            )
        novo = _TO_SHIPMENT.get(resultado.status, atual)
        volumes[n - 1]["status"] = novo.value
        estados.append(novo)
    remessa.parcels = volumes
    if not remessa.tracking_code and volumes and volumes[0].get("tracking_code"):
        remessa.tracking_code = volumes[0]["tracking_code"]
    destino = combine_statuses(estados)
    if destino == remessa.status:
        return False
    remessa.status = destino
    if destino == ShipmentStatus.POSTED and remessa.posted_at is None:
        remessa.posted_at = now
    if destino == ShipmentStatus.DELIVERED:
        remessa.delivered_at = now
        await _deliver_order(session, tenant, remessa, now)
    return True


async def _record(
    session: AsyncSession,
    remessa: OrderShipment,
    status: str,
    description: str,
    occurred_at: datetime,
) -> None:
    """Grava o evento; o mesmo evento duas vezes esbarra na chave natural e é ignorado."""
    session.add(
        ShipmentEvent(
            shipment_id=remessa.id,
            status=status,
            description=description[:200],
            occurred_at=occurred_at,
        )
    )
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()


async def _deliver_order(
    session: AsyncSession, tenant: TenantContext, remessa: OrderShipment, now: datetime
) -> None:
    pedido = await session.get(Order, remessa.order_id)
    if pedido is None:
        return
    await OrderService(session, tenant, ACTOR).carrier_delivered(pedido)
