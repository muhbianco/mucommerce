from __future__ import annotations

from app.workers.celery_app import celery_app


def test_beat_schedule_writes_under_tmp() -> None:
    assert celery_app.conf.beat_schedule_filename == "/tmp/celerybeat-schedule"
