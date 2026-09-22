"""Events: when and where a ticket product happens, and its lots of tickets.

Rules that live here (and nowhere else):
- only a product of kind `ticket` has an event (one); it is tracked, sold by unit;
- a lot is one variant of that product: its price is the variant's price, its tickets are the
  variant's stock, and `quantity` is how many tickets the lot was given. Creating a lot counts
  its stock to `quantity`; changing `quantity` adjusts the stock by the difference, never below
  the tickets already sold (the inventory ledger records both);
- the lots' quantities never add up to more than the event's capacity;
- the first lot takes over the product's default variant (its SKU), the next ones get
  `<SKU>-N`; removing a lot archives its variant, and only while none of its tickets sold;
- a lot sells between its window and the end of the event (`lot_state`, a pure function the
  storefront and the checkout share).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.catalog.models import (
    PUBLISHED_STATUSES,
    Event,
    EventLot,
    EventStatus,
    Product,
    ProductKind,
    ProductStatus,
    ProductVariant,
    SoldBy,
    StockPolicy,
    VariantStatus,
)
from app.catalog.repository import CatalogRepository
from app.catalog.schemas import MAX_LOTS, EventUpsert, LotCreate, LotState, LotUpdate
from app.catalog.skus import next_variant_sku
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.inventory.models import InventoryBalance
from app.inventory.schemas import AdjustmentCreate, AdjustmentLine
from app.inventory.service import InventoryService
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

MILLI = 1000


def lot_state(
    *,
    event: Event,
    lot: EventLot,
    available: int,
    variant_status: str,
    product_status: str,
    now: datetime,
) -> LotState:
    """Whether tickets of this lot can be bought right now, and if not, why."""
    if (
        event.status != EventStatus.SCHEDULED
        or product_status != ProductStatus.ACTIVE
        or variant_status != VariantStatus.ACTIVE
    ):
        return "unavailable"
    event_end = event.ends_at or event.starts_at
    closes = min(lot.sales_ends_at, event_end) if lot.sales_ends_at else event_end
    if now >= closes:
        return "ended"
    if lot.sales_starts_at is not None and now < lot.sales_starts_at:
        return "upcoming"
    if available <= 0:
        return "sold_out"
    return "on_sale"


@dataclass(frozen=True, slots=True)
class LotView:
    lot: EventLot
    variant: ProductVariant
    available: int
    state: LotState


@dataclass(frozen=True, slots=True)
class EventView:
    product: Product
    event: Event
    lots: list[LotView]

    @property
    def allocated(self) -> int:
        return sum(view.lot.quantity for view in self.lots)


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_product(self, product_id: str) -> Event | None:
        stmt = select(Event).where(Event.product_id == product_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def lots(self, event_id: str) -> list[tuple[EventLot, ProductVariant, int]]:
        """Lots in order, with their variant and tickets left (on hand minus reserved)."""
        stmt = (
            select(
                EventLot,
                ProductVariant,
                func.coalesce(InventoryBalance.on_hand_milli - InventoryBalance.reserved_milli, 0),
            )
            .join(ProductVariant, ProductVariant.id == EventLot.variant_id)
            .outerjoin(InventoryBalance, InventoryBalance.variant_id == EventLot.variant_id)
            .where(EventLot.event_id == event_id)
            .order_by(EventLot.position, EventLot.id)
            .limit(MAX_LOTS)
        )
        return [
            (lot, variant, int(left) // MILLI)
            for lot, variant, left in (await self.session.execute(stmt)).all()
        ]

    async def on_hand(self, variant_id: str) -> int:
        stmt = select(InventoryBalance.on_hand_milli).where(
            InventoryBalance.variant_id == variant_id
        )
        return int((await self.session.execute(stmt)).scalar_one_or_none() or 0) // MILLI

    async def has_lots(self, product_id: str) -> bool:
        stmt = (
            select(EventLot.id)
            .join(Event, Event.id == EventLot.event_id)
            .where(Event.product_id == product_id)
            .limit(1)
        )
        return (await self.session.execute(stmt)).first() is not None


class EventService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.repo = EventRepository(session)
        self.catalog = CatalogRepository(session)

    # ------------------------------------------------------------------ read
    async def get(self, product_id: str) -> EventView:
        product = await self._ticket_or_404(product_id)
        event = await self.repo.for_product(product.id)
        if event is None:
            raise NotFoundError("Evento ainda não configurado.")
        return await self._view(product, event)

    # ------------------------------------------------------------------ event
    async def upsert(self, product_id: str, data: EventUpsert) -> EventView:
        product = await self._ticket_or_404(product_id, lock=True)
        if data.ends_at is not None and data.ends_at <= data.starts_at:
            raise ValidationError("Fim do evento deve ser depois do início.", fields=["ends_at"])
        if not (data.venue_name or data.online_url):
            raise ValidationError(
                "Informe o local ou o link do evento online.", fields=["venue_name", "online_url"]
            )
        event = await self.repo.for_product(product.id)
        values = data.model_dump()
        if event is None:
            event = Event(
                product_id=product.id,
                **values,
                created_by_actor=self.actor.id,
                updated_by_actor=self.actor.id,
            )
            self.session.add(event)
            before: dict[str, Any] | None = None
            action = "event.created"
        else:
            view = await self._view(product, event)
            if data.capacity is not None and view.allocated > data.capacity:
                raise ConflictError(
                    "Capacidade menor que os ingressos já distribuídos nos lotes.",
                    code="capacity_below_lots",
                    allocated=view.allocated,
                )
            before = {name: _json(getattr(event, name)) for name in values}
            if before == {name: _json(value) for name, value in values.items()}:
                return view
            for name, value in values.items():
                setattr(event, name, value)
            event.updated_by_actor = self.actor.id
            action = "event.updated"
        # Tickets are counted units: the stock rules (and the ledger) apply to them.
        product.stock_policy = StockPolicy.TRACKED
        product.sold_by = SoldBy.UNIT
        product.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(action, event, before=before, after=_snapshot(event, values))
        await self._emit(product, "product.updated")
        return await self._view(product, event)

    # ------------------------------------------------------------------ lots
    async def add_lot(self, product_id: str, data: LotCreate) -> EventView:
        product = await self._ticket_or_404(product_id, lock=True)
        event = await self._event_or_404(product.id)
        view = await self._view(product, event)
        if len(view.lots) >= MAX_LOTS:
            raise ConflictError("Limite de lotes atingido.", limit=MAX_LOTS)
        _check_window(data.sales_starts_at, data.sales_ends_at)
        self._check_capacity(event, view.allocated + data.quantity)

        taken = {v.variant.id for v in view.lots}
        live = (await self.catalog.variants_for([product.id])).get(product.id, [])
        spare = next((v for v in live if v.id not in taken and not view.lots), None)
        if spare is None:
            skus = await self.catalog.skus_starting_with(f"{product.sku}-")
            spare = ProductVariant(
                product_id=product.id,
                sku=next_variant_sku(product.sku, skus),
                created_by_actor=self.actor.id,
            )
            self.session.add(spare)
        spare.name = data.name
        spare.price_cents = data.price_cents
        spare.option_values = None
        spare.status = VariantStatus.ACTIVE
        spare.position = len(view.lots)
        spare.updated_by_actor = self.actor.id
        await self.session.flush()
        lot = EventLot(
            event_id=event.id,
            variant_id=spare.id,
            quantity=data.quantity,
            sales_starts_at=data.sales_starts_at,
            sales_ends_at=data.sales_ends_at,
            position=len(view.lots),
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(lot)
        await self.session.flush()
        await self._stock(
            "count", spare.id, data.quantity, reason=f"Lote {data.name}: {data.quantity} ingressos"
        )
        await self._audit(
            "event.lot_created",
            event,
            after={"lot_id": lot.id, "sku": spare.sku, **_lot_snapshot(lot, spare)},
        )
        await self._emit(product, "product.updated")
        return await self._view(product, event)

    async def update_lot(self, product_id: str, lot_id: str, data: LotUpdate) -> EventView:
        product = await self._ticket_or_404(product_id, lock=True)
        event = await self._event_or_404(product.id)
        view = await self._view(product, event)
        current = next((v for v in view.lots if v.lot.id == lot_id), None)
        if current is None:
            raise NotFoundError("Lote não encontrado.")
        lot, variant = current.lot, current.variant
        changes = data.model_dump(exclude_unset=True)
        for name in ("name", "price_cents", "quantity"):
            if name in changes and changes[name] is None:
                raise ValidationError("Campos obrigatórios não podem ser nulos.", fields=[name])
        _check_window(
            changes.get("sales_starts_at", lot.sales_starts_at),
            changes.get("sales_ends_at", lot.sales_ends_at),
        )
        before = _lot_snapshot(lot, variant)
        if "quantity" in changes and changes["quantity"] != lot.quantity:
            delta = changes["quantity"] - lot.quantity
            self._check_capacity(event, view.allocated + delta)
            # Adjusting by the difference keeps sold tickets sold; the inventory refuses to go
            # below zero, i.e. below what was already sold.
            await self._stock(
                "adjustment",
                variant.id,
                delta,
                reason=f"Lote {variant.name}: de {lot.quantity} para {changes['quantity']}",
            )
            lot.quantity = changes["quantity"]
        if "name" in changes:
            variant.name = changes["name"]
        if "price_cents" in changes:
            variant.price_cents = changes["price_cents"]
        for name in ("sales_starts_at", "sales_ends_at"):
            if name in changes:
                setattr(lot, name, changes[name])
        after = _lot_snapshot(lot, variant)
        if after == before:
            return view
        lot.updated_by_actor = self.actor.id
        variant.updated_by_actor = self.actor.id
        await self.session.flush()
        await self._audit(
            "event.lot_updated",
            event,
            before={"lot_id": lot.id, **{k: v for k, v in before.items() if after[k] != v}},
            after={"lot_id": lot.id, **{k: v for k, v in after.items() if before[k] != v}},
        )
        await self._emit(product, "product.updated")
        return await self._view(product, event)

    async def remove_lot(self, product_id: str, lot_id: str) -> EventView:
        product = await self._ticket_or_404(product_id, lock=True)
        event = await self._event_or_404(product.id)
        view = await self._view(product, event)
        current = next((v for v in view.lots if v.lot.id == lot_id), None)
        if current is None:
            raise NotFoundError("Lote não encontrado.")
        if await self.repo.on_hand(current.variant.id) < current.lot.quantity:
            raise ConflictError("Lote com ingressos vendidos não pode ser removido.", code="sold")
        if product.status in PUBLISHED_STATUSES and len(view.lots) == 1:
            raise ConflictError("Evento publicado precisa de ao menos um lote.")
        variant = current.variant
        variant.archived_at = utcnow()
        variant.status = VariantStatus.INACTIVE
        variant.updated_by_actor = self.actor.id
        snapshot = _lot_snapshot(current.lot, variant)
        await self.session.delete(current.lot)
        await self.session.flush()
        await self._audit(
            "event.lot_removed", event, before={"lot_id": lot_id, "sku": variant.sku, **snapshot}
        )
        await self._emit(product, "product.updated")
        return await self._view(product, event)

    # ------------------------------------------------------------------ helpers
    async def _view(self, product: Product, event: Event) -> EventView:
        now = utcnow()
        lots = [
            LotView(
                lot,
                variant,
                available,
                lot_state(
                    event=event,
                    lot=lot,
                    available=available,
                    variant_status=variant.status,
                    product_status=product.status,
                    now=now,
                ),
            )
            for lot, variant, available in await self.repo.lots(event.id)
        ]
        return EventView(product, event, lots)

    async def _ticket_or_404(self, product_id: str, *, lock: bool = False) -> Product:
        product = await self.catalog.get_product(product_id, lock=lock)
        if product is None:
            raise NotFoundError("Produto não encontrado.")
        if product.kind != ProductKind.TICKET:
            raise ConflictError("Só produto do tipo ingresso tem evento.", code="not_a_ticket")
        if product.status == ProductStatus.ARCHIVED:
            raise ConflictError("Produto arquivado não pode ser editado.")
        return product

    async def _event_or_404(self, product_id: str) -> Event:
        event = await self.repo.for_product(product_id)
        if event is None:
            raise NotFoundError("Evento ainda não configurado.")
        return event

    @staticmethod
    def _check_capacity(event: Event, allocated: int) -> None:
        if event.capacity is not None and allocated > event.capacity:
            raise ConflictError(
                "Os lotes passariam da capacidade do evento.",
                code="over_capacity",
                capacity=event.capacity,
                allocated=allocated,
            )

    async def _stock(
        self, kind: Literal["count", "adjustment"], variant_id: str, quantity: int, *, reason: str
    ) -> None:
        if kind == "adjustment" and quantity == 0:
            return
        await InventoryService(self.session, self.tenant, self.actor).adjust(
            AdjustmentCreate(
                kind=kind,
                reason=reason[:200],
                lines=[AdjustmentLine(variant_id=variant_id, quantity=Decimal(quantity))],
            )
        )

    async def _audit(
        self,
        action: str,
        event: Event,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await audit(
            self.session,
            actor=self.actor.id,
            action=action,
            entity_type="event",
            entity_id=event.id,
            tenant_id=self.tenant.id,
            before=before,
            after=after,
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )

    async def _emit(self, product: Product, event_type: str) -> None:
        await emit(
            self.session,
            aggregate_type="product",
            aggregate_id=product.id,
            event_type=event_type,
            payload={
                "product_id": product.id,
                "sku": product.sku,
                "slug": product.slug,
                "status": product.status,
            },
            tenant_id=self.tenant.id,
        )


def _check_window(starts: datetime | None, ends: datetime | None) -> None:
    if starts is not None and ends is not None and ends <= starts:
        raise ValidationError(
            "Fim das vendas do lote deve ser depois do início.", fields=["sales_ends_at"]
        )


def _json(value: Any) -> Any:
    # Same instant, same text: an offset of -03:00 on input is not a change.
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value


def _snapshot(event: Event, names: Sequence[str] | dict[str, Any]) -> dict[str, Any]:
    return {name: _json(getattr(event, name)) for name in names}


def _lot_snapshot(lot: EventLot, variant: ProductVariant) -> dict[str, Any]:
    return {
        "name": variant.name,
        "price_cents": variant.price_cents,
        "quantity": lot.quantity,
        "sales_starts_at": _json(lot.sales_starts_at),
        "sales_ends_at": _json(lot.sales_ends_at),
    }
