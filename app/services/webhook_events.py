from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import WebhookEvent


def persist_webhook_event(raw_body_text: str, data: Any, db: Session) -> dict:
    status_tag = "EVENT_RECEIVED" if isinstance(data, dict) and data.get("object") == "instagram" else "IGNORED"

    source = data.get("object") if isinstance(data, dict) else None
    event_type = status_tag

    external_event_id = None
    try:
        if isinstance(data, dict):
            entries = data.get("entry", [])
            if entries:
                first = entries[0]
                if first.get("messaging"):
                    evt = first["messaging"][0]
                    external_event_id = (evt.get("message") or {}).get("mid")
                elif first.get("changes"):
                    change_value = first["changes"][0].get("value", {})
                    msg = (change_value.get("messages") or [{}])[0]
                    external_event_id = msg.get("id")
    except Exception:
        external_event_id = None

    event = WebhookEvent(
        source=source or "unknown",
        event_type=event_type,
        external_event_id=external_event_id,
        payload=data if isinstance(data, (dict, list)) else None,
        processed=(status_tag == "EVENT_RECEIVED"),
        created_at=datetime.utcnow(),
    )

    db.add(event)
    db.flush()
    return {"status_tag": status_tag, "event_id": event.id, "existing": False}
