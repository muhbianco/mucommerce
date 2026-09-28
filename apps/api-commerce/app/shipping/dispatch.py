"""Despacho: o operador manda o pedido embora e a etiqueta nasce (etapa J.3, ADR 0015).

Comprar etiqueta é **gasto irreversível** e o agregador não tem chave de idempotência. Quem
garante uma etiqueta por pedido é a linha de `order_shipments`, única por pedido: ela é
gravada *antes* da chamada, no estado `creating`. Clique duplo, duas abas ou retry do
navegador esbarram nessa linha em vez de comprarem dois fretes.

A ordem também importa: primeiro o provedor, depois o pedido. Se o pedido virasse `shipped`
antes da compra e a compra falhasse, a loja veria "enviado" sem etiqueta nenhuma.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ShipmentInProgressError,
    ShippingBalanceError,
    ShippingUnavailableError,
    ValidationError,
)
from app.core.logging import get_logger
from app.core.scopes import Scope
from app.orders.models import Order, OrderItem
from app.orders.service import OrderService
from app.orders.state_machine import OrderStatus
from app.shipping import registry
from app.shipping.models import OrderShipment, ShipmentStatus
from app.shipping.packing import MissingDimensions
from app.shipping.provider import (
    InsufficientBalanceError,
    ShipmentRequest,
    ShippingCredentials,
    ShippingParty,
    ShippingProviderError,
)
from app.shipping.service import QuoteLine, parcels_for
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor
from app.tenancy.settings_schemas import ShippingOrigin, ShippingSettings, fulfillment_settings

logger = get_logger(__name__)

#: Estados em que o despacho já aconteceu: pedir de novo devolve o que existe.
_DONE = frozenset(
    {
        ShipmentStatus.PURCHASED,
        ShipmentStatus.POSTED,
        ShipmentStatus.IN_TRANSIT,
        ShipmentStatus.DELIVERED,
        ShipmentStatus.RETURNED,
    }
)


class ShipmentService:
    def __init__(
        self,
        session: AsyncSession,
        tenant: TenantContext,
        actor: Actor,
        now: datetime,
    ) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.now = now

    async def get(self, order_id: str) -> OrderShipment | None:
        stmt = select(OrderShipment).where(OrderShipment.order_id == order_id)
        return (await self.session.execute(stmt)).scalars().first()

    async def dispatch(self, order: Order, *, scopes: frozenset[str]) -> OrderShipment:
        """Compra a etiqueta e leva o pedido para `shipped`. Idempotente por pedido."""
        cfg = fulfillment_settings(self.tenant.settings).shipping
        self._check(order, cfg)
        remessa = await self._row(order, cfg)
        if remessa.status in _DONE:
            return remessa  # já despachado: devolve o que existe em vez de comprar de novo

        provider = registry.get_provider(cfg.provider)
        if provider is None:
            raise ShippingUnavailableError
        credenciais = await self._credentials(cfg)
        pedido = await self._request(order, remessa, cfg)
        try:
            resultado = await provider.ship(credenciais, pedido)
        except InsufficientBalanceError as exc:
            await self._fail(remessa, str(exc))
            raise ShippingBalanceError from exc
        except ShippingProviderError as exc:
            await self._fail(remessa, str(exc))
            raise ShippingUnavailableError from exc

        remessa.status = ShipmentStatus.PURCHASED
        remessa.provider_shipment_id = resultado.provider_shipment_id
        remessa.tracking_code = resultado.tracking_code or None
        remessa.label_url = resultado.label_url
        remessa.cost_cents = resultado.cost_cents
        remessa.carrier = resultado.carrier or remessa.carrier
        remessa.purchased_at = self.now
        remessa.last_error = None
        await self.session.flush()
        await self._mark_shipped(order, scopes=scopes)
        logger.info(
            "Pedido despachado",
            extra={
                "order_id": order.id,
                "provider": cfg.provider,
                "service": remessa.service_code,
                "cost_cents": resultado.cost_cents,
            },
        )
        return remessa

    # ------------------------------------------------------------------ interno

    def _check(self, order: Order, cfg: ShippingSettings) -> None:
        if order.fulfillment_type != "shipping":
            raise ValidationError("Este pedido não é de envio por transportadora.")
        if not cfg.enabled or cfg.origin is None:
            raise ValidationError("Configure o endereço de origem antes de despachar.")
        if order.status not in {OrderStatus.ACCEPTED, OrderStatus.IN_PRODUCTION}:
            raise ValidationError("O pedido precisa estar aceito ou em produção para despachar.")

    async def _row(self, order: Order, cfg: ShippingSettings) -> OrderShipment:
        existente = await self.get(order.id)
        if existente is not None:
            if existente.status == ShipmentStatus.CREATING:
                # Outra chamada está no meio da compra: não dá para saber se ela vai passar.
                raise ShipmentInProgressError
            if existente.status in _DONE:
                return existente
            existente.status = ShipmentStatus.CREATING  # falhou antes: tenta de novo na mesma linha
            existente.requested_by_actor = self.actor.id
            await self.session.flush()
            return existente
        escolhido = order.fulfillment or {}
        remessa = OrderShipment(
            order_id=order.id,
            provider=str(escolhido.get("provider") or cfg.provider),
            status=ShipmentStatus.CREATING,
            service_code=str(escolhido.get("service_code") or ""),
            service_name=str(escolhido.get("service_name") or ""),
            carrier=str(escolhido.get("carrier") or ""),
            charged_cents=int(order.delivery_fee_cents or 0),
            requested_by_actor=self.actor.id,
        )
        self.session.add(remessa)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # A restrição única falou mais rápido: outro clique chegou primeiro.
            raise ShipmentInProgressError from exc
        return remessa

    async def _credentials(self, cfg: ShippingSettings) -> ShippingCredentials:
        from app.core.config import settings
        from app.integrations.credentials import CredentialStore

        token = await CredentialStore(self.session, self.tenant.id).get(
            cfg.provider, "access_token"
        )
        if not token:
            raise ValidationError("Conecte a conta da transportadora antes de despachar.")
        return ShippingCredentials(
            secrets={"access_token": token},
            public_config={},
            sandbox=settings.environment != "production",
        )

    async def _request(
        self, order: Order, remessa: OrderShipment, cfg: ShippingSettings
    ) -> ShipmentRequest:
        itens = await self._items(order.id)
        try:
            volumes = await parcels_for(
                self.session,
                [QuoteLine(i.variant_id, i.quantity_milli) for i in itens],
                cfg,
            )
        except MissingDimensions as exc:
            raise ValidationError(
                "Produto sem peso ou medida: não dá para gerar a etiqueta.",
                variants=list(exc.variants),
            ) from exc
        if not volumes:
            raise ValidationError("Pedido sem volume para enviar.")
        remessa.parcels = [
            {
                "weight_grams": p.weight_grams,
                "width_mm": p.width_mm,
                "height_mm": p.height_mm,
                "depth_mm": p.depth_mm,
                "value_cents": p.value_cents,
            }
            for p in volumes
        ]
        assert cfg.origin is not None
        return ShipmentRequest(
            reference=remessa.id,
            service_code=remessa.service_code,
            sender=_sender(cfg.origin),
            recipient=_recipient(order),
            parcels=volumes,
            order_number=order.number,
            insurance_cents=sum(p.value_cents for p in volumes),
            notes=f"Pedido {order.number}",
        )

    async def _items(self, order_id: str) -> list[OrderItem]:
        stmt = (
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.line_no)
            .limit(500)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _fail(self, remessa: OrderShipment, motivo: str) -> None:
        remessa.status = ShipmentStatus.FAILED
        remessa.last_error = motivo[:300]
        await self.session.flush()

    async def _mark_shipped(self, order: Order, *, scopes: frozenset[str]) -> None:
        if order.status == OrderStatus.SHIPPED:
            return
        await OrderService(self.session, self.tenant, self.actor).transition(
            order,
            OrderStatus.SHIPPED,
            reason="etiqueta emitida",
            scopes=scopes | {Scope.ORDERS_TRANSITION},
        )


def _sender(origin: ShippingOrigin) -> ShippingParty:
    return ShippingParty(
        name=origin.name,
        postal_code=origin.postal_code,
        street=origin.address,
        number=origin.number,
        district=origin.district,
        city=origin.city,
        state=origin.state,
        complement=origin.complement,
        document=origin.document,
        phone=origin.phone,
        email=origin.email,
    )


def _recipient(order: Order) -> ShippingParty:
    endereco: dict[str, Any] = (order.fulfillment or {}).get("address") or {}
    contato: dict[str, Any] = order.customer_snapshot or {}
    if not endereco.get("postal_code"):
        raise ValidationError("O pedido não tem endereço de entrega.")
    return ShippingParty(
        name=str(endereco.get("recipient_name") or contato.get("name") or "Cliente"),
        postal_code=str(endereco.get("postal_code") or ""),
        street=str(endereco.get("street") or ""),
        number=str(endereco.get("number") or "s/n"),
        district=str(endereco.get("district") or ""),
        city=str(endereco.get("city") or ""),
        state=str(endereco.get("state") or ""),
        complement=endereco.get("complement"),
        phone=str(endereco.get("phone") or contato.get("phone") or "") or None,
        email=str(contato.get("email") or "") or None,
    )
