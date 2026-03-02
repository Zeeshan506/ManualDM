from typing import Any

from fastapi import BackgroundTasks

from app.core.database import SessionLocal
from app.db.models import ActivityLog


def _insert_activity_log(
    *,
    actor: str,
    action: str,
    details: str | None = None,
    lead_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    db = SessionLocal()
    try:
        entry = ActivityLog(
            action_type=action,
            actor_user_id=(metadata or {}).get("actor_user_id"),
            actor_username=(actor or "system").strip() or "system",
            actor_role=(metadata or {}).get("actor_role"),
            entity_type=(metadata or {}).get("entity_type") or ("lead" if lead_id is not None else None),
            entity_id=(metadata or {}).get("entity_id") or (str(lead_id) if lead_id is not None else None),
            details=details,
            payload=metadata,
        )
        db.add(entry)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"⚠️ Failed to persist activity log action={action}: {exc}")
    finally:
        db.close()


def enqueue_activity_log(
    background_tasks: BackgroundTasks,
    *,
    actor: str,
    action: str,
    details: str | None = None,
    lead_id: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    background_tasks.add_task(
        _insert_activity_log,
        actor=actor,
        action=action,
        details=details,
        lead_id=lead_id,
        metadata=metadata,
    )
