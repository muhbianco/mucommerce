"""Stock an order holds while it waits for payment (ADR 0011 §4).

reserve (place) → commit (payment approved: on_hand leaves with a sale_commit movement) |
release (cancelled before payment) | expire (deadline). A committed reservation may be
returned (paid order cancelled with restock); an expired one may be recovered (paid late, the
stock is still there). Invariant: a balance's reserved_milli equals the
sum of its active reservations; both only change while the balance row is locked, and balances
are always locked in variant id order (InventoryRepository.lock_balances), so two orders over
the same variants queue instead of deadlocking.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.outbox import emit
from app.inventory.models import (
    InventoryBalance,
    InventoryMovement,
    InventoryReservation,
    MovementType,
    ReservationStatus,
)
from app.inventory.repository import InventoryRepository
from app.models.base import utcnow
from app.tenancy.context import TenantContext
from app.tenancy.service import Actor


class ReservationService:
    def __init__(self, session: AsyncSession, tenant: TenantContext, actor: Actor) -> None:
        self.session = session
        self.tenant = tenant
        self.actor = actor

    async def reserve(
        self,
        order_id: str,
        needs: Mapping[str, int],
        balances: Mapping[str, InventoryBalance],
    ) -> None:
        """Hold `needs` (variant → milli) for the order. The caller locked `balances` and checked
        availability under that lock (PricingService with the locked balances)."""
        for variant_id in sorted(needs):
            quantity = needs[variant_id]
            balances[variant_id].reserved_milli += quantity
            self.session.add(
                InventoryReservation(
                    order_id=order_id,
                    variant_id=variant_id,
                    quantity_milli=quantity,
                    status=ReservationStatus.ACTIVE,
                )
            )
        await self.session.flush()

    async def commit(self, order_id: str) -> int:
        """The sale happened: active reservations leave the shelf. Returns lines committed."""
        reservations = await self._reservations(order_id, ReservationStatus.ACTIVE)
        if not reservations:
            return 0
        balances = await self._lock([r.variant_id for r in reservations])
        now = utcnow()
        low: list[str] = []
        for reservation in reservations:
            balance = balances[reservation.variant_id]
            before = balance.on_hand_milli
            balance.on_hand_milli -= reservation.quantity_milli
            balance.reserved_milli -= reservation.quantity_milli
            self._movement(
                reservation, MovementType.SALE_COMMIT, -reservation.quantity_milli, balance, now
            )
            reservation.status = ReservationStatus.COMMITTED
            reservation.committed_at = now
            if _crossed_below(balance, before):
                low.append(reservation.variant_id)
        await self.session.flush()
        for variant_id in low:
            await self._low_stock(variant_id, balances[variant_id])
        return len(reservations)

    async def release(self, order_id: str, *, reason: str, expired: bool = False) -> int:
        """Give the held stock back (cancelled before payment, or deadline passed)."""
        reservations = await self._reservations(order_id, ReservationStatus.ACTIVE)
        if not reservations:
            return 0
        balances = await self._lock([r.variant_id for r in reservations])
        now = utcnow()
        for reservation in reservations:
            balances[reservation.variant_id].reserved_milli -= reservation.quantity_milli
            reservation.status = (
                ReservationStatus.EXPIRED if expired else ReservationStatus.RELEASED
            )
            reservation.released_at = now
            reservation.release_reason = reason[:32]
        await self.session.flush()
        return len(reservations)

    async def recover(self, order_id: str) -> bool:
        """A payment arrived after the deadline: sell the expired lines after all, but only if
        every one of them is still available now (all or nothing). Returns whether it did."""
        reservations = await self._reservations(order_id, ReservationStatus.EXPIRED)
        if not reservations:
            return True  # nothing tracked: nothing to take from the shelf
        balances = await self._lock([r.variant_id for r in reservations])
        for reservation in reservations:
            balance = balances.get(reservation.variant_id)
            if balance is None:
                return False
            available = balance.on_hand_milli - balance.reserved_milli
            if available < reservation.quantity_milli:
                return False
        now = utcnow()
        low: list[str] = []
        for reservation in reservations:
            balance = balances[reservation.variant_id]
            before = balance.on_hand_milli
            balance.on_hand_milli -= reservation.quantity_milli
            self._movement(
                reservation, MovementType.SALE_COMMIT, -reservation.quantity_milli, balance, now
            )
            reservation.status = ReservationStatus.COMMITTED
            reservation.committed_at = now
            reservation.release_reason = None
            if _crossed_below(balance, before):
                low.append(reservation.variant_id)
        await self.session.flush()
        for variant_id in low:
            await self._low_stock(variant_id, balances[variant_id])
        return True

    async def return_stock(self, order_id: str, *, reason: str) -> int:
        """A paid order was cancelled with restock: committed stock comes back to the shelf."""
        reservations = await self._reservations(order_id, ReservationStatus.COMMITTED)
        if not reservations:
            return 0
        balances = await self._lock([r.variant_id for r in reservations])
        now = utcnow()
        for reservation in reservations:
            balance = balances[reservation.variant_id]
            balance.on_hand_milli += reservation.quantity_milli
            self._movement(
                reservation, MovementType.SALE_RETURN, reservation.quantity_milli, balance, now
            )
            reservation.status = ReservationStatus.RETURNED
            reservation.released_at = now
            reservation.release_reason = reason[:32]
        await self.session.flush()
        return len(reservations)

    # ------------------------------------------------------------------ helpers
    async def _reservations(self, order_id: str, status: str) -> list[InventoryReservation]:
        stmt = (
            select(InventoryReservation)
            .where(InventoryReservation.order_id == order_id)
            .where(InventoryReservation.status == status)
            .order_by(InventoryReservation.variant_id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def _lock(self, variant_ids: list[str]) -> dict[str, InventoryBalance]:
        return await InventoryRepository(self.session).lock_balances(self.tenant.id, variant_ids)

    def _movement(
        self,
        reservation: InventoryReservation,
        kind: str,
        quantity: int,
        balance: InventoryBalance,
        now: object,
    ) -> None:
        self.session.add(
            InventoryMovement(
                variant_id=reservation.variant_id,
                movement_type=kind,
                qty_milli=quantity,
                balance_after_milli=balance.on_hand_milli,
                reference_type="order",
                reference_id=reservation.order_id,
                actor=self.actor.id,
                occurred_at=now,
            )
        )

    async def _low_stock(self, variant_id: str, balance: InventoryBalance) -> None:
        await emit(
            self.session,
            aggregate_type="variant_stock",
            aggregate_id=variant_id,
            event_type="inventory.low_stock",
            payload={
                "variant_id": variant_id,
                "available_milli": balance.on_hand_milli - balance.reserved_milli,
            },
            tenant_id=self.tenant.id,
        )


def _crossed_below(balance: InventoryBalance, on_hand_before: int) -> bool:
    """A sale took the available stock below the store's minimum (it was at or above)."""
    if balance.min_level_milli is None:
        return False
    # Before the commit the sold units were reserved, so available did not change: the alert
    # is about on_hand reaching the minimum as goods physically leave.
    return on_hand_before >= balance.min_level_milli > balance.on_hand_milli
