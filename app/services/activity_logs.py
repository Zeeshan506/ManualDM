from typing import Any
import logging

from app.core.celery_app import celery_app
from app.core.logging import get_logger, log_event


logger = get_logger(__name__)


def enqueue_activity_log(
    _unused_background_tasks: Any = None,
    *,
    actor: str,
    action: str,
    details: str | None = None,
    lead_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_actor = (actor or "system").strip() or "system"
    safe_metadata = metadata or {}
    try:
        celery_app.send_task(
            "tasks.persist_audit_log",
            kwargs={
                "action_type": action,
                "actor_user_id": safe_metadata.get("actor_user_id"),
                "actor_username": safe_actor,
                "actor_role": safe_metadata.get("actor_role"),
                "entity_type": safe_metadata.get("entity_type") or ("lead" if lead_id is not None else None),
                "entity_id": safe_metadata.get("entity_id") or (str(lead_id) if lead_id is not None else None),
                "details": details,
                "payload": safe_metadata,
            },
        )
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "audit.enqueue_failed",
            action=action,
            error=str(exc),
        )
