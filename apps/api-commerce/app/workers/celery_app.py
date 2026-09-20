from __future__ import annotations

from celery import Celery

from app.core.config import settings

QUEUE_DEFAULT = "commerce.default"
QUEUE_OUTBOX = "commerce.outbox"
QUEUE_PAYMENTS = "commerce.payments"
QUEUE_NOTIFICATIONS = "commerce.notifications"
QUEUE_PROVISIONING = "commerce.provisioning"
QUEUE_MEDIA = "commerce.media"

celery_app = Celery(
    "api_commerce",
    broker=settings.celery_broker_url or None,
    backend=settings.celery_result_backend or None,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_default_queue=QUEUE_DEFAULT,
    task_routes={
        "app.workers.tasks.relay_outbox": {"queue": QUEUE_OUTBOX},
        "app.workers.tasks.deliver_event": {"queue": QUEUE_OUTBOX},
        "app.workers.tasks.retry_due_deliveries": {"queue": QUEUE_OUTBOX},
        "app.workers.tasks.verify_domains": {"queue": QUEUE_PROVISIONING},
        "app.workers.tasks.recheck_active_domains": {"queue": QUEUE_PROVISIONING},
    },
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=300,
    task_soft_time_limit=240,
    broker_connection_retry_on_startup=True,
    # Without a broker (dev/tests) tasks run inline and propagate errors.
    task_always_eager=not settings.celery_broker_url,
    task_eager_propagates=True,
    timezone="UTC",
    beat_schedule={
        "relay-outbox": {"task": "app.workers.tasks.relay_outbox", "schedule": 5.0},
        "retry-due-deliveries": {
            "task": "app.workers.tasks.retry_due_deliveries",
            "schedule": 30.0,
        },
        "verify-domains": {"task": "app.workers.tasks.verify_domains", "schedule": 300.0},
        "recheck-active-domains": {
            "task": "app.workers.tasks.recheck_active_domains",
            "schedule": 1800.0,
        },
    },
)
