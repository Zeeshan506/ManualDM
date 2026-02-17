from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
import json

from utils import compute_fingerprint
from models import WebhookEvent


def persist_webhook_event(raw_body_text: str, data: Any, db: Session) -> dict:
    """Persist or update a WebhookEvent and return a summary dict.

    Preserves the exact fields and commit/rollback behavior from the original
    implementation in `main.py`.
    """
    status_tag = "EVENT_RECEIVED" if isinstance(data, dict) and data.get("object") == "instagram" else "IGNORED"

    fingerprint = compute_fingerprint(data) if data is not None else None

    existing_event = None
    if fingerprint:
        existing_event = db.query(WebhookEvent).filter(WebhookEvent.fingerprint == fingerprint).first()

    if existing_event:
        existing_event.received_at = datetime.utcnow()
        existing_event.object = data.get("object") if isinstance(data, dict) else existing_event.object
        existing_event.status = status_tag
        existing_event.raw_payload = data if isinstance(data, (dict, list)) else existing_event.raw_payload
        existing_event.raw_body = raw_body_text
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        return {"status_tag": status_tag, "fingerprint": fingerprint, "existing": True, "event_id": existing_event.id}

    event = WebhookEvent(
        received_at=datetime.utcnow(),
        object=data.get("object") if isinstance(data, dict) else None,
        status=status_tag,
        raw_payload=data if isinstance(data, (dict, list)) else None,
        raw_body=raw_body_text,
        fingerprint=fingerprint,
    )

    try:
        db.add(event)
        db.commit()
        return {"status_tag": status_tag, "fingerprint": fingerprint, "existing": False, "event_id": event.id}
    except Exception:
        db.rollback()
        raise
