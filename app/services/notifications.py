import json
import logging
from datetime import datetime
from typing import Any

import redis
from sqlalchemy.orm import Session

from app.core.logging import get_logger, log_event
from app.core.websockets import REDIS_URL
from app.db.models import NotificationEvent

logger = get_logger(__name__)


def build_notification_payload(event: NotificationEvent) -> dict[str, Any]:
    return {
        "id": int(event.id),
        "event_type": event.event_type,
        "title": event.title,
        "body": event.body,
        "lead_id": event.lead_id,
        "payload": event.payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "type": "system_notification",
    }


def create_notification_event(
    db: Session,
    *,
    event_type: str,
    title: str,
    body: str | None = None,
    lead_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> NotificationEvent:
    event = NotificationEvent(
        event_type=event_type,
        title=title,
        body=body,
        lead_id=lead_id,
        payload=payload,
        created_at=datetime.utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def publish_notification_payload(notification_payload: dict[str, Any]) -> None:
    try:
        redis_client = redis.Redis.from_url(REDIS_URL or "redis://localhost:6379/0")
        redis_client.publish("system_notifications", json.dumps(notification_payload))
        redis_client.close()
        log_event(
            logger,
            logging.INFO,
            "notification.publish_success",
            notification_id=notification_payload.get("id"),
            event_type=notification_payload.get("event_type"),
            lead_id=notification_payload.get("lead_id"),
        )
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "notification.publish_failed",
            notification_id=notification_payload.get("id"),
            event_type=notification_payload.get("event_type"),
            lead_id=notification_payload.get("lead_id"),
            error=str(exc),
        )
