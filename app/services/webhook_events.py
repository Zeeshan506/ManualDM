from datetime import datetime
import hashlib
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.db.models import WebhookEvent


def _is_duplicate_primary_key_error(exc: IntegrityError) -> bool:
    message = str(getattr(exc, "orig", exc)).lower()
    return "duplicate key value" in message and "(id)=" in message


def _sync_webhook_events_id_sequence(db: Session) -> None:
    sequence_name = db.execute(
        text("SELECT pg_get_serial_sequence('webhook_events', 'id')")
    ).scalar()
    if not sequence_name:
        return

    db.execute(
        text(
            "SELECT setval(:seq, COALESCE((SELECT MAX(id) FROM webhook_events), 0) + 1, false)"
        ),
        {"seq": sequence_name},
    )


def _idempotency_key(*, source: str, external_event_id: str | None, raw_body_text: str) -> str:
    if external_event_id:
        return f"{source}:{external_event_id}"
    body_hash = hashlib.sha256(raw_body_text.encode("utf-8")).hexdigest()
    return f"{source}:hash:{body_hash}"


def persist_webhook_event(raw_body_text: str, data: Any, status_tag: str, db: Session) -> dict:

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

    source_value = source or "unknown"
    idempotency_key = _idempotency_key(
        source=source_value,
        external_event_id=external_event_id,
        raw_body_text=raw_body_text,
    )

    existing_event = db.query(WebhookEvent).filter(WebhookEvent.idempotency_key == idempotency_key).first()
    if existing_event:
        return {
            "status_tag": existing_event.event_type or status_tag,
            "event_id": existing_event.id,
            "existing": True,
            "enqueue_status": existing_event.enqueue_status,
            "processing_state": existing_event.processing_state,
        }

    is_event_received = status_tag == "EVENT_RECEIVED"
    processing_state = "received" if is_event_received else "ignored"
    enqueue_status = "pending" if is_event_received else "skipped"

    event = WebhookEvent(
        source=source_value,
        event_type=event_type,
        external_event_id=external_event_id,
        idempotency_key=idempotency_key,
        payload=data if isinstance(data, (dict, list)) else None,
        processed=not is_event_received,
        processing_state=processing_state,
        enqueue_status=enqueue_status,
        enqueue_attempts=0,
        processing_attempts=0,
        queued_at=None,
        processed_at=None,
        next_retry_at=None,
        last_error=None,
        created_at=datetime.utcnow(),
    )

    db.add(event)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _is_duplicate_primary_key_error(exc):
            _sync_webhook_events_id_sequence(db)
            db.add(event)
            db.flush()
            return {
                "status_tag": status_tag,
                "event_id": event.id,
                "existing": False,
                "enqueue_status": event.enqueue_status,
                "processing_state": event.processing_state,
            }

        existing_event = db.query(WebhookEvent).filter(WebhookEvent.idempotency_key == idempotency_key).first()
        if existing_event:
            return {
                "status_tag": existing_event.event_type or status_tag,
                "event_id": existing_event.id,
                "existing": True,
                "enqueue_status": existing_event.enqueue_status,
                "processing_state": existing_event.processing_state,
            }
        raise

    return {
        "status_tag": status_tag,
        "event_id": event.id,
        "existing": False,
        "enqueue_status": event.enqueue_status,
        "processing_state": event.processing_state,
    }
