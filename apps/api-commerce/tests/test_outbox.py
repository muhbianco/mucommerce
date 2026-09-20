from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit import outbox
from app.audit.models import (
    DeliveryStatus,
    OutboxDelivery,
    OutboxEvent,
    OutboxStatus,
    ProcessedEvent,
)
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION
from tests.conftest import create_tenant


class _Recorder:
    def __init__(self, fail_times: int = 0) -> None:
        self.calls: list[str] = []
        self.fail_times = fail_times

    async def handler(self, session: AsyncSession, event: OutboxEvent) -> None:
        del session
        self.calls.append(event.id)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("boom")


async def _relay_and_deliver(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[str, str]]:
    dispatched: list[tuple[str, str]] = []

    async def dispatcher(event_id: str, consumer: str) -> None:
        dispatched.append((event_id, consumer))

    async with session_factory() as session:
        await outbox.relay_pending(session, dispatcher)
        await session.commit()
    for event_id, consumer in dispatched:
        async with session_factory() as session:
            await outbox.deliver(session, event_id, consumer)
            await session.commit()
    return dispatched


async def test_event_is_written_in_same_transaction_and_delivered_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recorder = _Recorder()
    outbox.registry.clear()
    outbox.registry.register("recorder", ("tenant.*",), recorder.handler)
    try:
        tenant = await create_tenant(session_factory, "alpha")  # emits tenant.created
        dispatched = await _relay_and_deliver(session_factory)
        assert dispatched == [(dispatched[0][0], "recorder")]
        assert recorder.calls == [dispatched[0][0]]

        # Second delivery of the same event is a no-op.
        async with session_factory() as session:
            status = await outbox.deliver(session, dispatched[0][0], "recorder")
            assert status == "duplicate"
            event = await session.get(OutboxEvent, dispatched[0][0])
            assert event is not None and event.status == OutboxStatus.DONE
            assert event.tenant_id == tenant.id
            processed = await session.get(
                ProcessedEvent, {"consumer": "recorder", "event_id": dispatched[0][0]}
            )
            assert processed is not None
        assert recorder.calls == [dispatched[0][0]]
    finally:
        outbox.registry.clear()


async def test_failing_consumer_backs_off_then_lands_in_dlq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    recorder = _Recorder(fail_times=99)
    outbox.registry.clear()
    outbox.registry.register("flaky", ("tenant.created",), recorder.handler)
    try:
        await create_tenant(session_factory, "alpha")
        dispatched = await _relay_and_deliver(session_factory)
        event_id = dispatched[0][0]

        async with session_factory() as session:
            delivery = (
                await session.execute(
                    select(OutboxDelivery)
                    .where(OutboxDelivery.event_id == event_id)
                    .execution_options(**{CROSS_TENANT_OPTION: True})
                )
            ).scalar_one()
            assert delivery.status == DeliveryStatus.PENDING
            assert delivery.attempts == 1
            assert delivery.next_attempt_at is not None
            assert delivery.next_attempt_at > utcnow() + timedelta(seconds=10)
            assert "boom" in (delivery.last_error or "")

        for _ in range(outbox.MAX_ATTEMPTS - 1):
            async with session_factory() as session:
                await outbox.deliver(session, event_id, "flaky")
                await session.commit()

        async with session_factory() as session:
            event = await session.get(OutboxEvent, event_id)
            assert event is not None and event.status == OutboxStatus.FAILED
            delivery = (
                await session.execute(
                    select(OutboxDelivery)
                    .where(OutboxDelivery.event_id == event_id)
                    .execution_options(**{CROSS_TENANT_OPTION: True})
                )
            ).scalar_one()
            assert delivery.status == DeliveryStatus.FAILED

            # Manual retry from the ops panel resets the delivery.
            await outbox.retry_failed(session, event_id)
            await session.commit()

        recorder.fail_times = 0
        async with session_factory() as session:
            assert await outbox.deliver(session, event_id, "flaky") == "done"
            await session.commit()
            event = await session.get(OutboxEvent, event_id)
            assert event is not None and event.status == OutboxStatus.DONE
    finally:
        outbox.registry.clear()


async def test_events_without_consumers_are_marked_done(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    outbox.registry.clear()
    try:
        await create_tenant(session_factory, "alpha")
        dispatched = await _relay_and_deliver(session_factory)
        assert dispatched == []
        async with session_factory() as session:
            events = (
                (
                    await session.execute(
                        select(OutboxEvent).execution_options(**{CROSS_TENANT_OPTION: True})
                    )
                )
                .scalars()
                .all()
            )
            assert events and all(e.status == OutboxStatus.DONE for e in events)
    finally:
        outbox.registry.clear()


async def test_sequence_increments_per_aggregate(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        for n in range(3):
            await outbox.emit(
                session,
                aggregate_type="order",
                aggregate_id="order-1",
                event_type="order.status_changed",
                payload={"n": n},
                tenant_id=None,
            )
            await session.flush()
        await outbox.emit(
            session,
            aggregate_type="order",
            aggregate_id="order-2",
            event_type="order.placed",
            payload={},
            tenant_id=None,
        )
        await session.commit()
        rows = (
            await session.execute(
                select(OutboxEvent.aggregate_id, OutboxEvent.sequence)
                .order_by(OutboxEvent.aggregate_id, OutboxEvent.sequence)
                .execution_options(**{CROSS_TENANT_OPTION: True})
            )
        ).all()
        assert [tuple(r) for r in rows] == [
            ("order-1", 1),
            ("order-1", 2),
            ("order-1", 3),
            ("order-2", 1),
        ]
