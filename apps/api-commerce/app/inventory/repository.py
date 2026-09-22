from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog.models import Product, ProductStatus, ProductVariant, StockPolicy
from app.core.ids import new_id
from app.core.sql import insert_if_missing, is_mariadb
from app.inventory.models import InventoryBalance, InventoryMovement
from app.tenancy.context import CROSS_TENANT_OPTION

LEDGER_AUDIT_BATCH = 500


@dataclass(frozen=True, slots=True)
class StockRow:
    variant: ProductVariant
    product: Product
    balance: InventoryBalance | None


def tracked_condition() -> ColumnElement[bool]:
    """Effective stock policy: the variant's own, else the product's."""
    return func.coalesce(ProductVariant.stock_policy, Product.stock_policy) == StockPolicy.TRACKED


class InventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def variants_with_products(
        self, variant_ids: Collection[str]
    ) -> dict[str, tuple[ProductVariant, Product]]:
        """Live (not archived) variants of live products, by id."""
        if not variant_ids:
            return {}
        stmt = (
            select(ProductVariant, Product)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(ProductVariant.id.in_(list(variant_ids)))
            .where(ProductVariant.archived_at.is_(None))
            .where(Product.status != ProductStatus.ARCHIVED)
        )
        return {v.id: (v, p) for v, p in (await self.session.execute(stmt)).tuples()}

    async def lock_balances(
        self, tenant_id: str, variant_ids: Collection[str]
    ) -> dict[str, InventoryBalance]:
        """Create missing balance rows (race-free), then lock them in id order.

        The deterministic order means two adjustments touching the same variants in any
        order queue behind each other instead of deadlocking.
        """
        ordered = sorted(set(variant_ids))
        if not ordered:
            # An empty executemany would insert one row of defaults (no variant_id).
            return {}
        rows = [
            {
                "id": new_id(),
                "tenant_id": tenant_id,
                "variant_id": variant_id,
                "on_hand_milli": 0,
                "reserved_milli": 0,
            }
            for variant_id in ordered
        ]
        await self.session.execute(
            insert_if_missing(
                self.session, InventoryBalance.__table__, rows, keep_column="on_hand_milli"
            )
        )
        stmt = (
            select(InventoryBalance)
            .where(InventoryBalance.variant_id.in_(ordered))
            .order_by(InventoryBalance.variant_id)
            .execution_options(populate_existing=True)
        )
        if is_mariadb(self.session):
            stmt = stmt.with_for_update()
        return {b.variant_id: b for b in (await self.session.execute(stmt)).scalars()}

    async def list_stock(
        self, *, limit: int, after_sku: str | None, low_only: bool, q: str | None
    ) -> Sequence[StockRow]:
        """Tracked variants with their balance (none yet = zero), by SKU; `limit + 1` rows."""
        stmt = (
            select(ProductVariant, Product, InventoryBalance)
            .join(Product, Product.id == ProductVariant.product_id)
            .outerjoin(
                InventoryBalance,
                and_(
                    InventoryBalance.variant_id == ProductVariant.id,
                    InventoryBalance.tenant_id == ProductVariant.tenant_id,
                ),
            )
            .where(ProductVariant.archived_at.is_(None))
            .where(Product.status != ProductStatus.ARCHIVED)
            .where(tracked_condition())
            .order_by(ProductVariant.sku)
            .limit(limit + 1)
        )
        if after_sku:
            stmt = stmt.where(ProductVariant.sku > after_sku)
        if q:
            stmt = stmt.where(
                Product.name.contains(q, autoescape=True)
                | ProductVariant.sku.startswith(q.upper(), autoescape=True)
            )
        if low_only:
            stmt = stmt.where(InventoryBalance.min_level_milli.is_not(None)).where(
                InventoryBalance.on_hand_milli - InventoryBalance.reserved_milli
                < InventoryBalance.min_level_milli
            )
        return [StockRow(v, p, b) for v, p, b in (await self.session.execute(stmt)).tuples()]

    async def balances_for(self, variant_ids: Collection[str]) -> dict[str, InventoryBalance]:
        if not variant_ids:
            return {}
        stmt = select(InventoryBalance).where(InventoryBalance.variant_id.in_(list(variant_ids)))
        return {b.variant_id: b for b in (await self.session.execute(stmt)).scalars()}

    async def movements(
        self, variant_id: str, *, limit: int, before_id: str | None
    ) -> Sequence[InventoryMovement]:
        stmt = (
            select(InventoryMovement)
            .where(InventoryMovement.variant_id == variant_id)
            .order_by(InventoryMovement.id.desc())
            .limit(limit + 1)
        )
        if before_id:
            stmt = stmt.where(InventoryMovement.id < before_id)
        return (await self.session.execute(stmt)).scalars().all()

    # --- maintenance (cross-tenant, explicit) ----------------------------------------
    async def ledger_mismatches(
        self, *, after_id: str | None
    ) -> tuple[list[dict[str, object]], str | None]:
        """One batch of balances whose ledger sum differs from `on_hand`; plus the cursor."""
        cross = {CROSS_TENANT_OPTION: True}
        batch_stmt = (
            select(InventoryBalance)
            .order_by(InventoryBalance.id)
            .limit(LEDGER_AUDIT_BATCH)
            .execution_options(**cross)
        )
        if after_id:
            batch_stmt = batch_stmt.where(InventoryBalance.id > after_id)
        balances = list((await self.session.execute(batch_stmt)).scalars())
        if not balances:
            return [], None
        sums_stmt = (
            select(
                InventoryMovement.tenant_id,
                InventoryMovement.variant_id,
                func.coalesce(func.sum(InventoryMovement.qty_milli), 0),
            )
            .where(InventoryMovement.variant_id.in_([b.variant_id for b in balances]))
            .group_by(InventoryMovement.tenant_id, InventoryMovement.variant_id)
            .execution_options(**cross)
        )
        sums = {(t, v): int(s) for t, v, s in (await self.session.execute(sums_stmt)).tuples()}
        mismatches: list[dict[str, object]] = [
            {
                "tenant_id": b.tenant_id,
                "variant_id": b.variant_id,
                "on_hand_milli": b.on_hand_milli,
                "ledger_milli": sums.get((b.tenant_id, b.variant_id), 0),
            }
            for b in balances
            if sums.get((b.tenant_id, b.variant_id), 0) != b.on_hand_milli
        ]
        return mismatches, balances[-1].id
