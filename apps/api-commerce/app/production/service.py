"""Insumos: entrada de compra, ajuste e o custo médio que sai disso.

Toda escrita de saldo passa por aqui e sempre do mesmo jeito: **trava a linha do insumo, grava
o movimento, atualiza o saldo**. Nessa ordem e sob a trava, porque `balance_after_milli` só vale
alguma coisa se ninguém mexeu no saldo entre ler e escrever — é esse campo que deixa auditar o
razão somando os movimentos.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.base import utcnow
from app.production import costing
from app.production.models import (
    Supplier,
    Supply,
    SupplyMovement,
    SupplyMovementType,
    SupplyReceipt,
)
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor

#: Teto de linhas numa compra: nota de mercado cabe folgado, e evita lote gigante numa transação.
MAX_LINES = 100


@dataclass(frozen=True, slots=True)
class ReceiptLine:
    supply_id: str
    qty_milli: int
    #: Quanto foi pago por esta linha, no total. `None` = entrada sem preço (não mexe no médio).
    total_cents: int | None = None


@dataclass(frozen=True, slots=True)
class AdjustmentLine:
    supply_id: str
    qty_milli: int
    reason: str | None = None


class SupplyService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor

    # ------------------------------------------------------------------ leitura

    async def supplies(self, *, active_only: bool = False, limit: int = 200) -> list[Supply]:
        stmt = select(Supply).order_by(Supply.name).limit(min(limit, 500))
        if active_only:
            stmt = stmt.where(Supply.active.is_(True))
        return list((await self.session.execute(stmt)).scalars())

    async def suppliers(self, *, limit: int = 200) -> list[Supplier]:
        stmt = select(Supplier).order_by(Supplier.name).limit(min(limit, 500))
        return list((await self.session.execute(stmt)).scalars())

    async def movements(self, supply_id: str, *, limit: int = 50) -> list[SupplyMovement]:
        stmt = (
            select(SupplyMovement)
            .where(SupplyMovement.supply_id == supply_id)
            .order_by(SupplyMovement.occurred_at.desc(), SupplyMovement.id.desc())
            .limit(min(limit, 200))
        )
        return list((await self.session.execute(stmt)).scalars())

    async def low_stock(self) -> list[Supply]:
        """Insumo abaixo do mínimo que a loja definiu — o alerta que evita parar a produção."""
        stmt = (
            select(Supply)
            .where(Supply.active.is_(True))
            .where(Supply.min_level_milli.is_not(None))
            .where(Supply.on_hand_milli <= Supply.min_level_milli)
            .order_by(Supply.name)
            .limit(200)
        )
        return list((await self.session.execute(stmt)).scalars())

    # ------------------------------------------------------------------ escrita

    async def receive(
        self,
        lines: Sequence[ReceiptLine],
        *,
        supplier_id: str | None = None,
        document: str | None = None,
        note: str | None = None,
        occurred_at: datetime | None = None,
    ) -> SupplyReceipt:
        """Uma compra: entra quantidade, sai custo médio novo."""
        self._check_lines(lines)
        quando = occurred_at or utcnow()
        if supplier_id is not None:
            await self._supplier(supplier_id)
        recibo = SupplyReceipt(
            supplier_id=supplier_id,
            document=document,
            note=note,
            occurred_at=quando,
            line_count=len(lines),
            total_cents=sum(line.total_cents or 0 for line in lines),
        )
        self.session.add(recibo)
        await self.session.flush()
        for line in lines:
            insumo = await self._locked(line.supply_id)
            custo = (
                costing.unit_cost_micro(line.total_cents, line.qty_milli)
                if line.total_cents
                else None
            )
            insumo.avg_cost_micro = costing.moving_average(
                on_hand_milli=insumo.on_hand_milli,
                avg_cost_micro=insumo.avg_cost_micro,
                qty_milli=line.qty_milli,
                entry_cost_micro=custo,
            )
            await self._move(
                insumo,
                SupplyMovementType.RECEIPT,
                line.qty_milli,
                unit_cost_micro=custo,
                reference_type="supply_receipt",
                reference_id=recibo.id,
                occurred_at=quando,
            )
        await self.session.flush()
        return recibo

    async def adjust(
        self, lines: Sequence[AdjustmentLine], *, kind: str, note: str | None = None
    ) -> SupplyReceipt:
        """Perda, contagem ou correção. Nunca mexe no custo médio: o que mudou foi quantidade."""
        if kind not in {
            SupplyMovementType.ADJUSTMENT,
            SupplyMovementType.LOSS,
            SupplyMovementType.COUNT,
        }:
            raise ValidationError("Tipo de ajuste inválido.", kind=kind)
        self._check_lines(lines, allow_negative=kind != SupplyMovementType.COUNT)
        quando = utcnow()
        recibo = SupplyReceipt(
            document=None, note=note, occurred_at=quando, line_count=len(lines), total_cents=0
        )
        self.session.add(recibo)
        await self.session.flush()
        for line in lines:
            insumo = await self._locked(line.supply_id)
            # Contagem é absoluta: o movimento é a diferença até o valor contado.
            delta = (
                line.qty_milli - insumo.on_hand_milli
                if kind == SupplyMovementType.COUNT
                else line.qty_milli
            )
            if delta == 0:
                continue
            await self._move(
                insumo,
                kind,
                delta,
                unit_cost_micro=None,
                reference_type="supply_adjustment",
                reference_id=recibo.id,
                reason=line.reason,
                occurred_at=quando,
            )
        await self.session.flush()
        return recibo

    async def consume(
        self, supply_id: str, qty_milli: int, *, reference_type: str, reference_id: str
    ) -> int:
        """Baixa de produção. Devolve quanto custou, em centavos, pelo médio do momento."""
        if qty_milli <= 0:
            return 0
        insumo = await self._locked(supply_id)
        custo = costing.cost_of(qty_milli, insumo.avg_cost_micro)
        await self._move(
            insumo,
            SupplyMovementType.PRODUCTION_OUT,
            -qty_milli,
            unit_cost_micro=insumo.avg_cost_micro,
            reference_type=reference_type,
            reference_id=reference_id,
        )
        return custo

    async def audit(self) -> list[tuple[str, int, int]]:
        """Insumos em que a soma dos movimentos não bate com o saldo. Deve voltar vazio."""
        somas = (
            select(
                SupplyMovement.supply_id.label("supply_id"),
                func.sum(SupplyMovement.qty_milli).label("total"),
            )
            .group_by(SupplyMovement.supply_id)
            .subquery()
        )
        stmt = select(Supply.id, Supply.on_hand_milli, func.coalesce(somas.c.total, 0)).join(
            somas, somas.c.supply_id == Supply.id, isouter=True
        )
        return [
            (str(sid), int(saldo), int(total))
            for sid, saldo, total in (await self.session.execute(stmt)).all()
            if int(saldo) != int(total)
        ]

    # ------------------------------------------------------------------ interno

    def _check_lines(self, lines: Sequence[object], *, allow_negative: bool = True) -> None:
        if not lines:
            raise ValidationError("Informe ao menos uma linha.")
        if len(lines) > MAX_LINES:
            raise ValidationError("Compra com linhas demais.", max_lines=MAX_LINES)
        for line in lines:
            qty = getattr(line, "qty_milli", 0)
            if qty == 0 or (qty < 0 and not allow_negative):
                raise ValidationError(
                    "Quantidade inválida.", supply_id=getattr(line, "supply_id", "")
                )

    async def _locked(self, supply_id: str) -> Supply:
        insumo = await self.session.scalar(
            select(Supply)
            .where(Supply.id == supply_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if insumo is None:
            raise NotFoundError("Insumo não encontrado.")
        return insumo

    async def _supplier(self, supplier_id: str) -> Supplier:
        fornecedor = await self.session.get(Supplier, supplier_id)
        if fornecedor is None:
            raise NotFoundError("Fornecedor não encontrado.")
        return fornecedor

    async def _move(
        self,
        supply: Supply,
        movement_type: str,
        qty_milli: int,
        *,
        unit_cost_micro: int | None,
        reference_type: str,
        reference_id: str,
        reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> SupplyMovement:
        supply.on_hand_milli += qty_milli
        movimento = SupplyMovement(
            supply_id=supply.id,
            movement_type=movement_type,
            qty_milli=qty_milli,
            balance_after_milli=supply.on_hand_milli,
            unit_cost_micro=unit_cost_micro,
            reference_type=reference_type,
            reference_id=reference_id,
            reason=reason,
            actor=self.actor.id,
            occurred_at=occurred_at or utcnow(),
        )
        self.session.add(movimento)
        await self.session.flush()
        return movimento
