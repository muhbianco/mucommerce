"""Despacho: o operador manda o pedido embora e a etiqueta nasce (etapa J.3, ADR 0015).

Comprar etiqueta é **gasto irreversível** e o agregador não tem chave de idempotência. Quem
garante uma etiqueta por pedido é a linha de `order_shipments`, única por pedido: ela é
gravada *antes* da chamada, no estado `creating`. Clique duplo, duas abas ou retry do
navegador esbarram nessa linha em vez de comprarem dois fretes.

A ordem também importa: primeiro o provedor, depois o pedido. Se o pedido virasse `shipped`
antes da compra e a compra falhasse, a loja veria "enviado" sem etiqueta nenhuma.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import PHYSICAL_KINDS
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
from app.shipping.declaration import (
    OrderLine,
    line_name,
    order_declaration,
    parcel_declarations,
)
from app.shipping.models import OrderShipment, ShipmentStatus
from app.shipping.packing import MissingDimensions
from app.shipping.plan import parcels_from_snapshot
from app.shipping.provider import (
    DeclaredItem,
    InsufficientBalanceError,
    Parcel,
    ShipmentRequest,
    ShippingCredentials,
    ShippingParty,
    ShippingProviderError,
)
from app.shipping.service import QuoteLine, ShippingQuoteService, customer_price, parcels_for
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


#: Despacho por volume parado em `creating` há mais que isto foi interrompido (processo caiu
#: no meio): dá para retomar, comprando só os volumes que ainda não têm etiqueta.
STALE_CREATING = timedelta(minutes=10)

#: Etiqueta que hoje sai mais que isto (%) acima do que foi cotado no checkout pede confirmação
#: antes da compra: o frete subiu depois que o cliente pagou.
CONFIRM_ABOVE_PERCENT = 10


@dataclass(frozen=True, slots=True)
class ShipmentPreview:
    """Quanto a etiqueta custa agora, antes de comprar (a ADR 0015 prometia mostrar)."""

    #: Quantas etiquetas serão compradas (uma por volume nos Correios).
    labels: int
    #: O que o cliente pagou de frete (0 com frete grátis).
    charged_cents: int
    #: Preço da transportadora hoje, sem acréscimo; `None` = não deu para cotar.
    price_cents: int | None = None
    #: Quanto o preço de hoje, com o acréscimo da loja, passa do cotado no checkout (%).
    increase_percent: int | None = None
    problem: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.increase_percent is not None and self.increase_percent > CONFIRM_ABOVE_PERCENT


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
        # Primeiro a remessa, depois as regras: pedido já despachado responde o que existe em
        # vez de reclamar do estado (ele virou `shipped` por causa do próprio despacho).
        pronta = await self.get(order.id)
        if pronta is not None and pronta.status in _DONE:
            return pronta
        self._check(order, cfg)
        remessa = await self._row(order, cfg)

        provider = registry.get_provider(cfg.provider)
        if provider is None:
            raise ShippingUnavailableError
        credenciais = await self._credentials(cfg)
        pedido = await self._request(order, remessa, cfg)
        congelado = (order.fulfillment or {}).get("parcel_plan") or {}
        if congelado.get("label_mode") == "per_volume":
            return await self._dispatch_per_volume(
                order, remessa, provider, credenciais, pedido, scopes=scopes
            )
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

    async def preview(self, order: Order) -> ShipmentPreview:
        """Cota de novo os volumes do pedido (o plano congelado, ou o recálculo do v1) no serviço
        que o cliente escolheu. Só leitura: não compra nada.

        A comparação é com o preço **cotado** no checkout, não com o frete cobrado: com frete
        grátis a loja paga tudo de qualquer jeito; o que interessa é se o frete subiu depois.
        """
        cfg = fulfillment_settings(self.tenant.settings).shipping
        escolhido = order.fulfillment or {}
        congelado = escolhido.get("parcel_plan")
        cobrado = int(order.delivery_fee_cents or 0)
        material = 0
        if isinstance(congelado, dict):
            volumes = parcels_from_snapshot(congelado)
            etiquetas = len(volumes) if congelado.get("label_mode") == "per_volume" else 1
            if cfg.packing.charge_material:
                material = sum(
                    int(v.get("material_cost_cents") or 0) for v in congelado.get("parcels") or []
                )
        else:
            etiquetas = 1
            try:
                volumes = await self._legacy_parcels(order, cfg)
            except ValidationError:
                return ShipmentPreview(etiquetas, cobrado, problem="missing_dimensions")
        destino = str((escolhido.get("address") or {}).get("postal_code") or "")
        if not volumes or not destino:
            return ShipmentPreview(etiquetas, cobrado, problem="no_parcels")
        opcoes, problema = await ShippingQuoteService(
            self.session, self.tenant, self.now
        ).quote_volumes(volumes, destination_postal_code=destino)
        servico = str(escolhido.get("service_code") or "")
        achada = next((o for o in opcoes or () if o.service_code == servico and o.usable), None)
        if achada is None:
            return ShipmentPreview(etiquetas, cobrado, problem=problema or "service_unavailable")
        cotado = int(escolhido.get("price_cents") or 0)
        aumento = None
        if cotado > 0:
            hoje = customer_price(achada.price_cents, material, cfg)
            aumento = (hoje - cotado) * 100 // cotado
        return ShipmentPreview(etiquetas, cobrado, achada.price_cents, aumento)

    async def _dispatch_per_volume(
        self,
        order: Order,
        remessa: OrderShipment,
        provider: Any,
        credentials: ShippingCredentials,
        template: ShipmentRequest,
        *,
        scopes: frozenset[str],
    ) -> OrderShipment:
        """Uma etiqueta por volume (Correios, J&T, Loggi: uma inserção de 1 volume cada).

        Cada etiqueta comprada é **gravada na hora** (commit): o Melhor Envio não tem chave de
        idempotência, e se o 2º volume falhar depois de o 1º ter sido pago, perder o registro do
        1º faria a próxima tentativa comprá-lo de novo. A retomada compra só o que falta. O
        pedido vira `shipped` quando todos os volumes têm etiqueta.
        """
        declaracoes = await self._declare(order, (order.fulfillment or {}).get("parcel_plan"))
        volumes = [dict(v) for v in (remessa.parcels or [])]
        total = len(volumes)
        for n, (volume, parcela) in enumerate(zip(volumes, template.parcels, strict=True), start=1):
            if volume.get("provider_shipment_id"):
                continue  # comprada numa tentativa anterior
            pedido = replace(
                template,
                reference=f"{remessa.id}.{n}",
                parcels=(parcela,),
                insurance_cents=parcela.value_cents,
                notes=f"Pedido {order.number} - volume {n} de {total}",
                # A DACE desta etiqueta lista só o que está neste volume.
                items=declaracoes[n - 1] if n - 1 < len(declaracoes) else template.items,
            )
            try:
                resultado = await provider.ship(credentials, pedido)
            except InsufficientBalanceError as exc:
                await self._fail_kept(remessa, volumes, f"volume {n} de {total}: {exc}")
                raise ShippingBalanceError from exc
            except ShippingProviderError as exc:
                await self._fail_kept(remessa, volumes, f"volume {n} de {total}: {exc}")
                raise ShippingUnavailableError from exc
            volumes[n - 1] = volume | {
                "provider_shipment_id": resultado.provider_shipment_id,
                "tracking_code": resultado.tracking_code or None,
                "label_url": resultado.label_url,
                "cost_cents": resultado.cost_cents,
                "status": ShipmentStatus.PURCHASED.value,
                "carrier": resultado.carrier or None,
            }
            remessa.parcels = [dict(v) for v in volumes]  # lista nova: o JSON muda de verdade
            await self.session.commit()

        primeiro = volumes[0] if volumes else {}
        remessa.status = ShipmentStatus.PURCHASED
        remessa.provider_shipment_id = primeiro.get("provider_shipment_id")
        remessa.tracking_code = primeiro.get("tracking_code")
        remessa.label_url = primeiro.get("label_url")
        remessa.cost_cents = sum(int(v.get("cost_cents") or 0) for v in volumes)
        remessa.carrier = str(primeiro.get("carrier") or remessa.carrier)
        remessa.purchased_at = self.now
        remessa.last_error = None
        await self.session.flush()
        await self._mark_shipped(order, scopes=scopes)
        logger.info(
            "Pedido despachado por volume",
            extra={
                "order_id": order.id,
                "service": remessa.service_code,
                "labels": total,
                "cost_cents": remessa.cost_cents,
            },
        )
        return remessa

    async def _fail_kept(
        self, remessa: OrderShipment, volumes: list[dict[str, Any]], motivo: str
    ) -> None:
        """Falhou no meio: grava (commit) as etiquetas já compradas e o motivo, antes de o
        erro desfazer a transação do pedido."""
        remessa.parcels = [dict(v) for v in volumes]
        await self._fail(remessa, motivo)
        await self.session.commit()

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
            parado = (
                existente.updated_at is not None
                and self.now - existente.updated_at > STALE_CREATING
            )
            por_volume = any(v.get("provider_shipment_id") for v in existente.parcels or [])
            if existente.status == ShipmentStatus.CREATING and not (parado and por_volume):
                # Outra chamada está no meio da compra: não dá para saber se ela vai passar.
                # Exceção: despacho por volume parado há tempo (processo caiu) com etiquetas já
                # gravadas — retoma comprando só as que faltam.
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
        congelado = (order.fulfillment or {}).get("parcel_plan")
        if isinstance(congelado, dict):
            # Frete v2: a etiqueta sai com os volumes que foram cotados e pagos — nunca recalcula
            # com a medida de hoje. Vale com a flag ligada ou não (desligar não muda pedido feito).
            volumes = parcels_from_snapshot(congelado)
        else:
            volumes = await self._legacy_parcels(order, cfg)
        if not volumes:
            raise ValidationError("Pedido sem volume para enviar.")
        novos = [
            {
                "weight_grams": p.weight_grams,
                "width_mm": p.width_mm,
                "height_mm": p.height_mm,
                "depth_mm": p.depth_mm,
                "value_cents": p.value_cents,
            }
            for p in volumes
        ]
        antigos = remessa.parcels or []
        # Retomada: as etiquetas já compradas (por volume) ficam; só se refaz o que não tem.
        if len(antigos) == len(novos):
            novos = [
                a if a.get("provider_shipment_id") else n
                for a, n in zip(antigos, novos, strict=True)
            ]
        remessa.parcels = novos
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
            items=order_declaration(await self._order_lines(order.id)),
        )

    async def _order_lines(self, order_id: str) -> list[OrderLine]:
        """As linhas que vão na caixa: digital, serviço e ingresso não entram na declaração."""
        return [
            OrderLine(
                variant_id=item.variant_id,
                name=line_name(item.product_name, item.variant_name),
                quantity_milli=item.quantity_milli,
                total_cents=item.total_cents,
                by_weight=item.sold_by == "weight",
                unit_label=item.unit_label,
            )
            for item in await self._items(order_id)
            if item.product_kind in PHYSICAL_KINDS
        ]

    async def _declare(self, order: Order, congelado: Any) -> tuple[tuple[DeclaredItem, ...], ...]:
        """Com uma etiqueta por volume, a declaração de cada uma (só o que está no volume)."""
        if not isinstance(congelado, dict) or congelado.get("label_mode") != "per_volume":
            return ()
        return parcel_declarations(
            await self._order_lines(order.id), congelado.get("parcels") or []
        )

    async def _legacy_parcels(self, order: Order, cfg: ShippingSettings) -> tuple[Parcel, ...]:
        """Pedido do motor v1 (sem plano congelado): recalcula como sempre fez."""
        itens = await self._items(order.id)
        try:
            return await parcels_for(
                self.session,
                [QuoteLine(i.variant_id, i.quantity_milli) for i in itens],
                cfg,
            )
        except MissingDimensions as exc:
            raise ValidationError(
                "Produto sem peso ou medida: não dá para gerar a etiqueta.",
                variants=list(exc.variants),
            ) from exc

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
