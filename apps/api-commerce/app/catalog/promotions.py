"""Promoção que acaba quando o estoque zera (docs/14-pedidos-lunares.md §1).

O estoque que conta é o físico (`on_hand`), não o disponível: um Pix abandonado segura unidades
sem que elas tenham saído, e a promoção não pode morrer por ele. O carimbo é feito por um
consumidor do outbox, depois do commit da baixa, e não dentro dela: decidir "todas as variações
zeraram" lê o saldo das irmãs, que a transação da baixa não travou. Duas baixas concorrentes em
variações diferentes veriam cada uma o saldo antigo da outra e ninguém encerraria; depois do
commit, o consumidor que roda por último enxerga os dois zeros.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.models import OutboxEvent
from app.audit.outbox import emit
from app.audit.writer import audit
from app.catalog.models import (
    Product,
    ProductStatus,
    ProductVariant,
    StockPolicy,
    VariantStatus,
)
from app.catalog.pricing import promo_is_active
from app.catalog.repository import CatalogRepository
from app.core.logging import get_logger
from app.inventory.models import InventoryBalance
from app.models.base import utcnow

logger = get_logger(__name__)

SOLD_OUT_EVENT = "inventory.sold_out"
ACTOR = "system:promo_sellout"


def promo_variants(variants: Iterable[ProductVariant], product_policy: str) -> list[ProductVariant]:
    """As variações cujo estoque decide a promoção: herdam o preço (e portanto a promoção),
    contam estoque e ainda podem ser vendidas. Pausada conta: ela volta."""
    return [
        v
        for v in variants
        if v.price_cents is None
        and v.archived_at is None
        and v.status != VariantStatus.INACTIVE
        and (v.stock_policy or product_policy) == StockPolicy.TRACKED
    ]


def crossed_to_zero(on_hand_before: int, on_hand_after: int) -> bool:
    """A baixa levou o estoque físico a zero (ou abaixo, numa contagem que achou menos)."""
    return on_hand_before > 0 >= on_hand_after


async def emit_sold_out(session: AsyncSession, tenant_id: str, variant_id: str) -> None:
    await emit(
        session,
        aggregate_type="variant_stock",
        aggregate_id=variant_id,
        event_type=SOLD_OUT_EVENT,
        payload={"variant_id": variant_id},
        tenant_id=tenant_id,
    )


async def end_promotion_on_sellout(session: AsyncSession, event: OutboxEvent) -> None:
    """Consumidor de `inventory.sold_out`. Idempotente: produto sem a opção, sem promoção
    rodando, já carimbado ou com alguma variação ainda em estoque não muda nada."""
    variant = await session.get(ProductVariant, str(event.payload.get("variant_id") or ""))
    if variant is None or variant.price_cents is not None:
        return  # variação com preço próprio nunca esteve na promoção
    product = await CatalogRepository(session).get_product(variant.product_id, lock=True)
    if (
        product is None
        or not product.promo_ends_on_sellout
        or product.promo_sold_out_at is not None
        or product.status == ProductStatus.ARCHIVED
    ):
        return
    now = utcnow()
    if not promo_is_active(
        base_cents=product.base_price_cents,
        promo_cents=product.promo_price_cents,
        starts_at=product.promo_starts_at,
        ends_at=product.promo_ends_at,
        now=now,
    ):
        return
    if not await _all_sold_out(session, product):
        return
    product.promo_sold_out_at = now
    await session.flush()
    await audit(
        session,
        actor=ACTOR,
        action="product.promo_sold_out",
        entity_type="product",
        entity_id=product.id,
        tenant_id=product.tenant_id,
        after={"promo_sold_out_at": now.isoformat(), "variant_id": variant.id},
    )
    await emit(
        session,
        aggregate_type="product",
        aggregate_id=product.id,
        event_type="product.updated",
        payload={
            "product_id": product.id,
            "sku": product.sku,
            "slug": product.slug,
            "status": product.status,
            "promo_sold_out": True,
        },
        tenant_id=product.tenant_id,
    )
    logger.info(
        "Promotion ended by sellout",
        extra={"tenant_id": product.tenant_id, "product_id": product.id},
    )


async def _all_sold_out(session: AsyncSession, product: Product) -> bool:
    variants = (
        (
            await session.execute(
                select(ProductVariant).where(ProductVariant.product_id == product.id)
            )
        )
        .scalars()
        .all()
    )
    counted = promo_variants(variants, product.stock_policy)
    if not counted:
        return False
    rows = await session.execute(
        select(InventoryBalance.variant_id, InventoryBalance.on_hand_milli).where(
            InventoryBalance.variant_id.in_([v.id for v in counted])
        )
    )
    on_hand = dict(rows.tuples().all())
    # Sem linha de saldo = nunca entrou estoque = zero.
    return all(on_hand.get(v.id, 0) <= 0 for v in counted)
