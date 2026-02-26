import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from celery import Celery
from dotenv import load_dotenv

load_dotenv()


def _redis_url_with_db(redis_url: str, db_index: int) -> str:
    parsed = urlparse(redis_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if "db" in query:
        query["db"] = str(db_index)
        return urlunparse(parsed._replace(query=urlencode(query)))
    return urlunparse(parsed._replace(path=f"/{db_index}"))


REDIS_CONNECTION_STRING = os.getenv("REDIS_CONNECTION_STRING")
DEFAULT_REDIS_BASE = REDIS_CONNECTION_STRING or "redis://127.0.0.1:6379"
BROKER_URL = os.getenv(
    "CELERY_BROKER_URL",
    _redis_url_with_db(DEFAULT_REDIS_BASE, 0),
)
RESULT_BACKEND = os.getenv(
    "CELERY_RESULT_BACKEND",
    _redis_url_with_db(DEFAULT_REDIS_BASE, 1),
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
)
