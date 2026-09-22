from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import beat_init, celeryd_init, setup_logging

from app.core.config import settings
from app.core.logging import configure_logging
from app.core.observability import setup_sentry

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
    include=[
        "app.workers.tasks",
        "app.workers.media",
        "app.workers.orders",
        "app.workers.payments",
        "app.workers.notifications",
    ],
)

celery_app.conf.update(
    task_default_queue=QUEUE_DEFAULT,
    task_routes={
        "app.workers.tasks.relay_outbox": {"queue": QUEUE_OUTBOX},
        "app.workers.tasks.deliver_event": {"queue": QUEUE_OUTBOX},
        "app.workers.tasks.retry_due_deliveries": {"queue": QUEUE_OUTBOX},
        "app.workers.tasks.verify_domains": {"queue": QUEUE_PROVISIONING},
        "app.workers.tasks.recheck_active_domains": {"queue": QUEUE_PROVISIONING},
        "app.workers.media.process_media": {"queue": QUEUE_MEDIA},
        "app.workers.media.sweep_media": {"queue": QUEUE_MEDIA},
        "app.workers.orders.expire_orders": {"queue": QUEUE_PAYMENTS},
        "app.workers.payments.process_webhook": {"queue": QUEUE_PAYMENTS},
        "app.workers.payments.sweep_webhooks": {"queue": QUEUE_PAYMENTS},
        "app.workers.payments.reconcile_payments": {"queue": QUEUE_PAYMENTS},
        "app.workers.payments.process_refund": {"queue": QUEUE_PAYMENTS},
        "app.workers.payments.sweep_refunds": {"queue": QUEUE_PAYMENTS},
        "app.workers.payments.payments_health_check": {"queue": QUEUE_PAYMENTS},
        "app.workers.notifications.send_notifications": {"queue": QUEUE_NOTIFICATIONS},
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
    # USER api cannot write celerybeat-schedule under WORKDIR /app.
    beat_schedule_filename="/tmp/celerybeat-schedule",  # noqa: S108  (private container /tmp)
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
        "sweep-media": {"task": "app.workers.media.sweep_media", "schedule": 120.0},
        "expire-orders": {"task": "app.workers.orders.expire_orders", "schedule": 60.0},
        "sweep-payment-webhooks": {
            "task": "app.workers.payments.sweep_webhooks",
            "schedule": 30.0,
        },
        "reconcile-payments": {
            "task": "app.workers.payments.reconcile_payments",
            "schedule": 60.0,
        },
        "sweep-refunds": {"task": "app.workers.payments.sweep_refunds", "schedule": 60.0},
        "send-notifications": {
            "task": "app.workers.notifications.send_notifications",
            "schedule": 15.0,
        },
        "payments-health-check": {
            "task": "app.workers.payments.payments_health_check",
            "schedule": 300.0,
        },
        "audit-inventory-ledger": {
            "task": "app.workers.tasks.audit_inventory_ledger",
            "schedule": 86400.0,
        },
        "purge-expired-records": {
            "task": "app.workers.tasks.purge_expired_records",
            "schedule": 86400.0,
        },
    },
)


@setup_logging.connect
def _configure_logging(**_: Any) -> None:
    """JSON logs like the API. Connecting this signal stops Celery from installing its own."""
    configure_logging(settings.log_level)


@celeryd_init.connect
@beat_init.connect
def _init_error_reporting(**_: Any) -> None:
    # Before the prefork pool forks, so every child inherits the client (CeleryIntegration).
    setup_sentry()
