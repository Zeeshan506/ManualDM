import os

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger, setup_logging
from kombu import Queue
from dotenv import load_dotenv
from app.core.logging import configure_logging
from app.core.redis_config import get_redis_base_url, redis_url_with_db

load_dotenv()

DEFAULT_REDIS_BASE = get_redis_base_url()
BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    redis_url_with_db(DEFAULT_REDIS_BASE, 0),
)
RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    redis_url_with_db(DEFAULT_REDIS_BASE, 1),
)

celery_app = Celery(
    "test_server",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["app.tasks.meta_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_always_eager=False,
    task_eager_propagates=False,
    task_default_queue="events",
    task_default_exchange="events",
    task_default_routing_key="events",
    task_queues=(
        Queue("events"),
        Queue("meta"),
        Queue("audit"),
    ),
    task_routes={
        "tasks.process_webhook_event": {"queue": "events"},
        "tasks.redrive_webhook_enqueue": {"queue": "events"},
        "tasks.send_automation_reply": {"queue": "events"},
        "tasks.post_meta_conversion_event": {"queue": "meta"},
        "tasks.persist_audit_log": {"queue": "audit"},
    },
    beat_schedule={
        "redrive-webhook-enqueue": {
            "task": "tasks.redrive_webhook_enqueue",
            "schedule": 30.0,
            "kwargs": {"batch_size": 200},
        }
    },
)


@setup_logging.connect
def _configure_celery_root_logging(*args, **kwargs):
    configure_logging()


@after_setup_logger.connect
def _configure_celery_logger(*args, **kwargs):
    configure_logging()


@after_setup_task_logger.connect
def _configure_celery_task_logger(*args, **kwargs):
    configure_logging()
