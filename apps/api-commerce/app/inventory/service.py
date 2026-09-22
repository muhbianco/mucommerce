"""Stock adjustments from the panel, stock reads, and the daily ledger check.

An adjustment is all-or-nothing: every line is validated, the balances are locked in variant
order (no deadlock between two adjustments touching the same variants), and if any line would
leave stock below zero nothing is written (409). Every change is a ledger movement carrying
the balance after it, so `SUM(qty_milli) == on_hand_milli` holds by construction; the daily
job proves it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.audit.writer import audit
from app.catalog.models import Product, ProductVariant, SoldBy, StockPolicy
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    ReservedStockError,
    ValidationError,
)
from app.core.logging import get_logger
from app.inventory.models import (
    MILLI,
    InventoryBalance,
    InventoryMovement,
    MovementType,
    StockAdjustment,
)
from app.inventory.repository import InventoryRepository, StockRow
from app.inventory.schemas import AdjustmentCreate, AdjustmentLine
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

logger = get_logger(__name__)

_MOVEMENT_FOR_KIND = {
    "receipt": MovementType.RECEIPT,
    "loss": MovementType.LOSS,
    "adjustment": MovementType.ADJUSTMENT,
    "count": MovementType.COUNT,
}
MICRO_PER_CENT = 10_000


def to_milli(quantity: Decimal) -> int:
    """Exact: the schema allows at most 3 decimals."""
    return int(quantity * MILLI)


def from_milli(value: int) -> Decimal:
    return (Decimal(value) / MILLI).quantize(Decimal("0.001"))


def effective_policy(variant: ProductVariant, product: Product) -> str:
    return variant.stock_policy or product.stock_policy


@dataclass(frozen=True, slots=True)
class AdjustmentResult:
    adjustment: StockAdjustment
    movements: list[InventoryMovement]


@dataclass(frozen=True, slots=True)
class StockView:
    row: StockRow

    @property
    def on_hand_milli(self) -> int:
        return self.row.balance.on_hand_milli if self.row.balance else 0

    @property
    def reserved_milli(self) -> int:
        return self.row.balance.reserved_milli if self.row.balance else 0

    @property
    def min_level_milli(self) -> int | None:
        return self.row.balance.min_level_milli if self.row.balance else None

    @property
    def available_milli(self) -> int:
        return self.on_hand_milli - self.reserved_milli

    @property
    def low_stock(self) -> bool:
        return self.min_level_milli is not None and self.available_milli < self.min_level_milli


class InventoryService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor
        self.repo = InventoryRepository(session)

    async def adjust(self, data: AdjustmentCreate) -> AdjustmentResult:
        if data.kind in {"adjustment", "loss"} and not data.reason:
            raise ValidationError("Informe o motivo.", fields=["reason"])
        ids = [line.variant_id for line in data.lines]
        if len(set(ids)) != len(ids):
            raise ValidationError("Cada variante só pode aparecer uma vez.")
        found = await self.repo.variants_with_products(ids)
        unknown = sorted(set(ids) - set(found))
        if unknown:
            raise ValidationError("Variante inexistente.", variant_ids=unknown)
        for line in data.lines:
            self._check_line(data.kind, line, *found[line.variant_id])

        balances = await self.repo.lock_balances(self.tenant.id, ids)
        new_levels: dict[str, int] = {}
        short: list[dict[str, Any]] = []
        reserved_short: list[dict[str, Any]] = []
        oversold: list[str] = []
        for line in data.lines:
            balance = balances[line.variant_id]
            qty = to_milli(line.quantity)
            delta = {
                "receipt": qty,
                "loss": -qty,
                "adjustment": qty,
                "count": qty - balance.on_hand_milli,
            }[data.kind]
            after = balance.on_hand_milli + delta
            if after < 0:
                short.append(
                    {
                        "variant_id": line.variant_id,
                        "sku": found[line.variant_id][0].sku,
                        "on_hand": str(from_milli(balance.on_hand_milli)),
                    }
                )
            elif after < balance.reserved_milli and data.kind in ("loss", "adjustment"):
                # Orders awaiting payment hold that stock: a manual change may not take it.
                # A count is the physical truth and goes through (it is flagged as oversold).
                reserved_short.append(
                    {
                        "variant_id": line.variant_id,
                        "sku": found[line.variant_id][0].sku,
                        "reserved": str(from_milli(balance.reserved_milli)),
                    }
                )
            elif after < balance.reserved_milli:
                oversold.append(line.variant_id)
            new_levels[line.variant_id] = after
        if short:
            raise ConflictError("Estoque insuficiente.", code="insufficient_stock", lines=short)
        if reserved_short:
            raise ReservedStockError(lines=reserved_short)

        adjustment = StockAdjustment(
            kind=data.kind,
            reason=data.reason,
            note=data.note,
            line_count=len(data.lines),
            created_by_actor=self.actor.id,
            updated_by_actor=self.actor.id,
        )
        self.session.add(adjustment)
        await self.session.flush()

        now = utcnow()
        movements: list[InventoryMovement] = []
        crossed_low: list[str] = []
        for line in data.lines:
            balance = balances[line.variant_id]
            before = balance.on_hand_milli
            after = new_levels[line.variant_id]
            movement = InventoryMovement(
                variant_id=line.variant_id,
                movement_type=_MOVEMENT_FOR_KIND[data.kind],
                qty_milli=after - before,
                balance_after_milli=after,
                unit_cost_micro=(
                    line.unit_cost_cents * MICRO_PER_CENT
                    if line.unit_cost_cents is not None
                    else None
                ),
                reference_type="stock_adjustment",
                reference_id=adjustment.id,
                reason=data.reason,
                actor=self.actor.id,
                occurred_at=now,
            )
            self.session.add(movement)
            movements.append(movement)
            balance.on_hand_milli = after
            if _crossed_below(balance, before, after):
                crossed_low.append(line.variant_id)
        await self.session.flush()

        lines_payload = [
            {
                "variant_id": m.variant_id,
                "delta_milli": m.qty_milli,
                "on_hand_milli": m.balance_after_milli,
            }
            for m in movements
        ]
        await audit(
            self.session,
            actor=self.actor.id,
            action="inventory.adjusted",
            entity_type="stock_adjustment",
            entity_id=adjustment.id,
            tenant_id=self.tenant.id,
            after={"kind": data.kind, "reason": data.reason, "lines": lines_payload},
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )
        await emit(
            self.session,
            aggregate_type="stock_adjustment",
            aggregate_id=adjustment.id,
            event_type="inventory.adjusted",
            payload={"adjustment_id": adjustment.id, "kind": data.kind, "lines": lines_payload},
            tenant_id=self.tenant.id,
        )
        for variant_id in oversold:
            await emit(
                self.session,
                aggregate_type="variant_stock",
                aggregate_id=variant_id,
                event_type="inventory.oversold",
                payload={
                    "variant_id": variant_id,
                    "sku": found[variant_id][0].sku,
                    "on_hand_milli": balances[variant_id].on_hand_milli,
                    "reserved_milli": balances[variant_id].reserved_milli,
                },
                tenant_id=self.tenant.id,
            )
        for variant_id in crossed_low:
            await emit(
                self.session,
                aggregate_type="variant_stock",
                aggregate_id=variant_id,
                event_type="inventory.low_stock",
                payload={
                    "variant_id": variant_id,
                    "sku": found[variant_id][0].sku,
                    "available_milli": balances[variant_id].on_hand_milli
                    - balances[variant_id].reserved_milli,
                },
                tenant_id=self.tenant.id,
            )
        return AdjustmentResult(adjustment, movements)

    async def list_stock(
        self, *, limit: int, after_sku: str | None, low_only: bool, q: str | None
    ) -> list[StockView]:
        rows = await self.repo.list_stock(limit=limit, after_sku=after_sku, low_only=low_only, q=q)
        return [StockView(row) for row in rows]

    async def movements(
        self, variant_id: str, *, limit: int, before_id: str | None
    ) -> list[InventoryMovement]:
        await self._variant_or_404(variant_id)
        return list(await self.repo.movements(variant_id, limit=limit, before_id=before_id))

    async def set_min_level(self, variant_id: str, min_level: Decimal | None) -> StockView:
        variant, product = await self._variant_or_404(variant_id)
        balance = (await self.repo.lock_balances(self.tenant.id, [variant_id]))[variant_id]
        before = balance.min_level_milli
        balance.min_level_milli = to_milli(min_level) if min_level is not None else None
        await self.session.flush()
        await audit(
            self.session,
            actor=self.actor.id,
            action="inventory.min_level_set",
            entity_type="product_variant",
            entity_id=variant_id,
            tenant_id=self.tenant.id,
            before={"min_level_milli": before},
            after={"min_level_milli": balance.min_level_milli},
            ip=self.actor.ip,
            user_agent=self.actor.user_agent,
        )
        return StockView(StockRow(variant, product, balance))

    async def _variant_or_404(self, variant_id: str) -> tuple[ProductVariant, Product]:
        found = await self.repo.variants_with_products([variant_id])
        if variant_id not in found:
            raise NotFoundError("Variante não encontrada.")
        return found[variant_id]

    @staticmethod
    def _check_line(
        kind: str, line: AdjustmentLine, variant: ProductVariant, product: Product
    ) -> None:
        if effective_policy(variant, product) != StockPolicy.TRACKED:
            raise ValidationError(
                "Produto não controla estoque.", variant_id=variant.id, sku=variant.sku
            )
        if product.sold_by == SoldBy.UNIT and line.quantity != line.quantity.to_integral_value():
            raise ValidationError(
                "Produto vendido por unidade: use quantidade inteira.", sku=variant.sku
            )
        if kind in {"receipt", "loss"} and line.quantity <= 0:
            raise ValidationError("Quantidade deve ser maior que zero.", sku=variant.sku)
        if kind == "adjustment" and line.quantity == 0:
            raise ValidationError("Ajuste com quantidade zero.", sku=variant.sku)
        if kind == "count" and line.quantity < 0:
            raise ValidationError("Contagem não pode ser negativa.", sku=variant.sku)
        if line.unit_cost_cents is not None and kind != "receipt":
            raise ValidationError("Custo só em entradas (receipt).", sku=variant.sku)


def _crossed_below(balance: InventoryBalance, before: int, after: int) -> bool:
    if balance.min_level_milli is None:
        return False
    available_before = before - balance.reserved_milli
    available_after = after - balance.reserved_milli
    return available_before >= balance.min_level_milli > available_after


async def audit_ledger(session: AsyncSession) -> int:
    """Daily, cross-tenant: every balance must equal the sum of its movements. Read-only;
    each mismatch is logged as an error (with ids only) for investigation."""
    repo = InventoryRepository(session)
    cursor: str | None = None
    mismatches = 0
    while True:
        found, cursor = await repo.ledger_mismatches(after_id=cursor)
        for item in found:
            logger.error("Inventory ledger mismatch", extra=item)
        mismatches += len(found)
        if cursor is None:
            return mismatches
