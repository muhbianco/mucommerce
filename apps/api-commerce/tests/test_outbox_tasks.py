"""Outbox through the real Celery task path (eager mode, file-backed SQLite).

These run as sync tests on purpose: the tasks own their event loop and engine
(`app.workers.runtime`), exactly like a worker process does.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit import outbox
from app.audit.models import (
    DeliveryStatus,
    OutboxDelivery,
    OutboxEvent,
    OutboxStatus,
    ProcessedEvent,
)
from app.core.config import settings
from app.models.all import Base
from app.models.base import utcnow
from app.tenancy.context import CROSS_TENANT_OPTION
from app.workers import tasks

API_ROOT = Path(__file__).resolve().parents[1]
CROSS = {CROSS_TENANT_OPTION: True}


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_times = 0

    async def handler(self, session: AsyncSession, event: OutboxEvent) -> None:
        del session
        self.calls.append(event.id)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("boom")


def _in_db[T](url: str, fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    async def _run() -> T:
        engine = create_async_engine(url)
        try:
            factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                result = await fn(session)
                await session.commit()
                return result
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@pytest.fixture
def task_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point the worker runtime (`with_session`) at a fresh file database."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'outbox.db').as_posix()}"

    async def _create() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create())
    monkeypatch.setattr(settings, "database_url_override", url)
    return url


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    saved = dict(outbox.registry.consumers)
    rec = _Recorder()
    outbox.registry.clear()
    outbox.registry.register("recorder", ("test.*",), rec.handler)
    yield rec
    outbox.registry.clear()
    outbox.registry.consumers.update(saved)


def _emit(url: str) -> str:
    async def _fn(session: AsyncSession) -> str:
        event = await outbox.emit(
            session,
            aggregate_type="test",
            aggregate_id="agg-1",
            event_type="test.happened",
            payload={},
            tenant_id=None,
        )
        await session.flush()
        return event.id

    return _in_db(url, _fn)


def _state(url: str, event_id: str) -> dict[str, Any]:
    async def _fn(session: AsyncSession) -> dict[str, Any]:
        event = await session.get(OutboxEvent, event_id)
        deliveries = list(
            (
                await session.execute(
                    select(OutboxDelivery)
                    .where(OutboxDelivery.event_id == event_id)
                    .execution_options(**CROSS)
                )
            ).scalars()
        )
        processed = await session.get(
            ProcessedEvent, {"consumer": "recorder", "event_id": event_id}
        )
        assert event is not None
        return {
            "event": event.status,
            "deliveries": [(d.status, d.attempts) for d in deliveries],
            "processed": processed is not None,
        }

    return _in_db(url, _fn)


def _make_deliveries_due(url: str) -> None:
    async def _fn(session: AsyncSession) -> None:
        await session.execute(
            update(OutboxDelivery)
            .values(next_attempt_at=utcnow() - timedelta(seconds=1))
            .execution_options(**CROSS)
        )

    _in_db(url, _fn)


def test_worker_boot_registers_outbox_consumers() -> None:
    """Regression: consumers were only imported by the FastAPI app, so the worker's
    registry was empty and every event was marked `done` without being delivered."""
    code = (
        "import sys\n"
        "from app.workers.celery_app import celery_app\n"
        "celery_app.loader.import_default_modules()\n"
        "from app.audit.outbox import registry\n"
        "sys.exit(0 if 'audit_projector' in registry.consumers else 1)\n"
    )
    result = subprocess.run(  # noqa: S603  (fixed code, current interpreter)
        [sys.executable, "-c", code],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


def test_relay_commits_deliveries_before_dispatching(
    task_db: str, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    del recorder
    event_id = _emit(task_db)
    visible_at_dispatch: list[bool] = []

    def spy(to_dispatch: outbox.DispatchList) -> None:
        async def _fn(session: AsyncSession) -> bool:
            row = (
                await session.execute(
                    select(OutboxDelivery.id)
                    .where(OutboxDelivery.event_id == event_id)
                    .execution_options(**CROSS)
                )
            ).first()
            return row is not None

        visible_at_dispatch.append(_in_db(task_db, _fn))
        assert to_dispatch == [(event_id, "recorder")]

    monkeypatch.setattr(tasks, "_dispatch", spy)
    assert tasks.relay_outbox() == 1
    assert visible_at_dispatch == [True]


def test_event_is_delivered_end_to_end_through_tasks(task_db: str, recorder: _Recorder) -> None:
    event_id = _emit(task_db)

    assert tasks.relay_outbox() == 1  # eager: deliver_event runs inline after the commit

    assert recorder.calls == [event_id]
    assert _state(task_db, event_id) == {
        "event": OutboxStatus.DONE,
        "deliveries": [(DeliveryStatus.DONE, 1)],
        "processed": True,
    }
    assert tasks.relay_outbox() == 0  # nothing relayed twice
    assert tasks.retry_due_deliveries() == 0


def test_manual_dlq_retry_is_redelivered_by_the_beat_task(
    task_db: str, recorder: _Recorder
) -> None:
    """Regression: `retry_failed` reset attempts to 0 but retries required attempts > 0."""
    recorder.fail_times = outbox.MAX_ATTEMPTS
    event_id = _emit(task_db)

    tasks.relay_outbox()  # attempt 1 fails
    for _ in range(outbox.MAX_ATTEMPTS - 1):
        _make_deliveries_due(task_db)
        assert tasks.retry_due_deliveries() == 1
    assert _state(task_db, event_id)["event"] == OutboxStatus.FAILED
    assert tasks.retry_due_deliveries() == 0  # the DLQ is not retried automatically

    async def _retry(session: AsyncSession) -> int:
        return await outbox.retry_failed(session, event_id)  # what POST /ops/outbox/retry does

    assert _in_db(task_db, _retry) == 1
    assert tasks.retry_due_deliveries() == 1

    assert _state(task_db, event_id) == {
        "event": OutboxStatus.DONE,
        "deliveries": [(DeliveryStatus.DONE, 1)],
        "processed": True,
    }


def test_lost_dispatch_is_recovered_after_the_lease(
    task_db: str, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    event_id = _emit(task_db)

    with monkeypatch.context() as m:
        m.setattr(tasks, "_dispatch", lambda to_dispatch: None)  # broker lost the message
        assert tasks.relay_outbox() == 1

    assert tasks.retry_due_deliveries() == 0  # still leased: no double dispatch in flight
    assert recorder.calls == []

    _make_deliveries_due(task_db)  # lease expired
    assert tasks.retry_due_deliveries() == 1
    assert recorder.calls == [event_id]
    assert _state(task_db, event_id)["event"] == OutboxStatus.DONE
