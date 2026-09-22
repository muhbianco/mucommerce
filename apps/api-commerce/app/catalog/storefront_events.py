"""Storefront read model of events: upcoming list, event page and sitemap entries.

Published ticket products only (active or paused); the session carries the tenant (resolved by
Host) and every query is batched per page. Lots come out as labels (`lot_state`), never as
ticket counts, and the link of an online event is not part of it (it goes to buyers only).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.events import lot_state
from app.catalog.models import (
    PUBLISHED_STATUSES,
    Event,
    EventLot,
    EventStatus,
    Product,
    ProductVariant,
)
from app.catalog.schemas import LotState
from app.inventory.models import InventoryBalance
from app.media.models import MediaAsset, MediaOwner
from app.media.repository import MediaRepository

EventAvailability = Literal[
    "on_sale", "upcoming", "sold_out", "ended", "unavailable", "postponed", "cancelled"
]
EVENTS_PAGE_MAX = 48
SITEMAP_MAX = 5000


@dataclass(frozen=True, slots=True)
class LotOffer:
    lot: EventLot
    variant: ProductVariant
    state: LotState


@dataclass(frozen=True, slots=True)
class EventData:
    event: Event
    product: Product
    images: list[MediaAsset]
    lots: list[LotOffer]

    @property
    def availability(self) -> EventAvailability:
        if self.event.status == EventStatus.POSTPONED:
            return "postponed"
        if self.event.status == EventStatus.CANCELLED:
            return "cancelled"
        states = {offer.state for offer in self.lots}
        for label in ("on_sale", "upcoming", "sold_out"):
            if label in states:
                return label
        return "ended" if states == {"ended"} else "unavailable"

    @property
    def price_from(self) -> int | None:
        """Cheapest lot on sale now, else the cheapest lot at all (what "a partir de" shows)."""
        on_sale = [o.variant.price_cents or 0 for o in self.lots if o.state == "on_sale"]
        every = [o.variant.price_cents or 0 for o in self.lots]
        prices = on_sale or every
        return min(prices) if prices else None


class StorefrontEvents:
    def __init__(self, session: AsyncSession, now: datetime) -> None:
        self.session = session
        self.now = now

    async def upcoming(
        self, *, limit: int, after: tuple[datetime, str] | None = None
    ) -> list[EventData]:
        """Events not over yet, by (starts_at, id); `limit + 1` rows for keyset paging."""
        stmt = (
            select(Event, Product)
            .join(Product, Product.id == Event.product_id)
            .where(Product.status.in_(PUBLISHED_STATUSES))
            .where(func.coalesce(Event.ends_at, Event.starts_at) > self.now)
            .order_by(Event.starts_at, Event.id)
            .limit(limit + 1)
        )
        if after is not None:
            starts_at, last_id = after
            stmt = stmt.where(
                or_(
                    Event.starts_at > starts_at,
                    and_(Event.starts_at == starts_at, Event.id > last_id),
                )
            )
        rows = [(event, product) for event, product in (await self.session.execute(stmt)).all()]
        return await self._data(rows, first_image_only=True)

    async def by_slug(self, slug: str) -> EventData | None:
        """The event page stays up after the event (its lots read `ended`)."""
        stmt = (
            select(Event, Product)
            .join(Product, Product.id == Event.product_id)
            .where(Product.slug == slug)
            .where(Product.status.in_(PUBLISHED_STATUSES))
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        [data] = await self._data([(row[0], row[1])], first_image_only=False)
        return data

    async def sitemap(self) -> list[tuple[str, datetime]]:
        stmt = (
            select(Product.slug, Product.updated_at)
            .join(Event, Event.product_id == Product.id)
            .where(Product.status.in_(PUBLISHED_STATUSES))
            .order_by(Event.starts_at.desc(), Event.id)
            .limit(SITEMAP_MAX)
        )
        return [(slug, updated) for slug, updated in (await self.session.execute(stmt)).all()]

    # ------------------------------------------------------------------ batch helpers
    async def _data(
        self, rows: Sequence[tuple[Event, Product]], *, first_image_only: bool
    ) -> list[EventData]:
        if not rows:
            return []
        lots = await self._lots([event.id for event, _ in rows])
        images = await MediaRepository(self.session).ready_for_owners(
            MediaOwner.PRODUCT, [product.id for _, product in rows]
        )
        result: list[EventData] = []
        for event, product in rows:
            product_images = images.get(product.id, [])
            offers = [
                LotOffer(
                    lot,
                    variant,
                    lot_state(
                        event=event,
                        lot=lot,
                        available=available,
                        variant_status=variant.status,
                        product_status=product.status,
                        now=self.now,
                    ),
                )
                for lot, variant, available in lots.get(event.id, [])
            ]
            result.append(
                EventData(
                    event,
                    product,
                    product_images[:1] if first_image_only else list(product_images),
                    offers,
                )
            )
        return result

    async def _lots(
        self, event_ids: Sequence[str]
    ) -> dict[str, list[tuple[EventLot, ProductVariant, int]]]:
        stmt = (
            select(
                EventLot,
                ProductVariant,
                func.coalesce(InventoryBalance.on_hand_milli - InventoryBalance.reserved_milli, 0),
            )
            .join(ProductVariant, ProductVariant.id == EventLot.variant_id)
            .outerjoin(InventoryBalance, InventoryBalance.variant_id == EventLot.variant_id)
            .where(EventLot.event_id.in_(list(event_ids)))
            .where(ProductVariant.archived_at.is_(None))
            .order_by(EventLot.event_id, EventLot.position, EventLot.id)
        )
        grouped: dict[str, list[tuple[EventLot, ProductVariant, int]]] = {}
        for lot, variant, left in (await self.session.execute(stmt)).all():
            grouped.setdefault(lot.event_id, []).append((lot, variant, int(left) // 1000))
        return grouped
