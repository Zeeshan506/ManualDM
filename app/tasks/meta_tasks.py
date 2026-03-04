import os
import logging
import json
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
import redis
from sqlalchemy import or_

from celery import Task

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.logging import get_logger, log_event
from app.core.websockets import REDIS_URL
from app.db.models import ActivityLog, Lead, PaymentEvent, WebhookEvent
from app.services.event_handlers import handle_event_received
from app.services.notifications import build_notification_payload, create_notification_event, publish_notification_payload
from app.services.post_leads_to_meta import post_meta_event_by_id
from utils import append_chat_message, automation_mail


class BaseDBTask(Task):
    abstract = True


logger = get_logger(__name__)


def _publish_new_message(lead_id: int, payload: Dict[str, Any]) -> None:
    if not lead_id:
        return
    try:
        redis_client = redis.Redis.from_url(REDIS_URL or "redis://localhost:6379/0")
        redis_client.publish(
            "chat_broadcasts",
            json.dumps({"lead_id": int(lead_id), "payload": payload}),
        )
        redis_client.close()
        log_event(
            logger,
            logging.INFO,
            "websocket.publish_success",
            lead_id=int(lead_id),
        )
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "websocket.publish_failed",
            lead_id=int(lead_id),
            error=str(exc),
        )


def _build_incoming_message_notification(*, lead: Lead | None, lead_id: int, message_payload: Dict[str, Any]) -> tuple[str, str | None, Dict[str, Any]]:
    lead_name = (lead.name or "").strip() if lead else ""
    lead_label = lead_name or f"Lead #{lead_id}"

    raw_text = message_payload.get("text") if isinstance(message_payload, dict) else None
    text = str(raw_text).strip() if raw_text is not None else ""
    body = text if text else "📩 New incoming message"

    if len(body) > 140:
        body = f"{body[:137]}..."

    title = f"New message from {lead_label}"
    payload = {
        "message_id": message_payload.get("id") if isinstance(message_payload, dict) else None,
        "direction": message_payload.get("direction") if isinstance(message_payload, dict) else None,
        "lead_id": int(lead_id),
    }

    return title, body, payload


def _repeat_count(task_name: str, default: int = 3) -> int:
    specific_key = f"{task_name.upper()}_REPEAT_COUNT"
    raw_value = os.getenv(specific_key) or os.getenv("REPEAT_COUNT") or os.getenv("TASK_REPEAT_COUNT")
    if not raw_value:
        return default
    try:
        parsed = int(raw_value)
        return max(0, parsed)
    except ValueError:
        return default


def _retry_or_finalize(task: Task, exc: Exception, task_name: str, details: Dict[str, Any]) -> Dict[str, Any]:
    max_retries = _repeat_count(task_name)
    current_retry = int(getattr(task.request, "retries", 0))

    if current_retry < max_retries:
        countdown = min(60, 2 ** current_retry)
        raise task.retry(exc=exc, countdown=countdown, max_retries=max_retries)

    log_event(
        logger,
        logging.ERROR,
        "task.failed_after_retries",
        task_name=task_name,
        retries=current_retry,
        details=details,
        error=str(exc),
    )
    return {
        "status": "failed",
        "task": task_name,
        "retries": current_retry,
        "error": str(exc),
        **details,
    }


@celery_app.task(bind=True, base=BaseDBTask, name="tasks.process_webhook_event")
def process_webhook_event(self, *, event_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        event = db.query(WebhookEvent).filter(WebhookEvent.id == int(event_id)).first()
        if not event:
            return {
                "status": "skipped",
                "task": "process_webhook_event",
                "event_id": int(event_id),
                "reason": "event_not_found",
            }

        if not isinstance(event.payload, dict):
            event.processing_state = "failed"
            event.enqueue_status = "failed"
            event.last_error = "invalid_payload"
            event.next_retry_at = datetime.utcnow() + timedelta(minutes=1)
            db.commit()
            return {
                "status": "skipped",
                "task": "process_webhook_event",
                "event_id": int(event_id),
                "reason": "invalid_payload",
            }

        if event.processing_state == "processed" or event.processed:
            return {
                "status": "skipped",
                "task": "process_webhook_event",
                "event_id": int(event_id),
                "reason": "already_processed",
            }

        event.processing_state = "processing"
        event.processing_attempts = int(event.processing_attempts or 0) + 1
        event.last_error = None
        db.commit()

        summary = handle_event_received(event.payload, db)
        event.processed = True
        event.processing_state = "processed"
        event.enqueue_status = "processed"
        event.processed_at = datetime.utcnow()
        event.next_retry_at = None
        event.last_error = None
        db.commit()

        lead_result = summary.get("lead_result") if isinstance(summary, dict) else None
        lead_id = int(lead_result["lead_id"]) if isinstance(lead_result, dict) and lead_result.get("lead_id") else None
        inbound_messages = summary.get("saved_inbound_messages") if isinstance(summary, dict) else []
        if lead_id and isinstance(inbound_messages, list):
            lead = db.query(Lead).filter(Lead.id == int(lead_id)).first()
            for payload in inbound_messages:
                if isinstance(payload, dict):
                    _publish_new_message(lead_id, payload)

                    if payload.get("direction") == "inbound":
                        title, body, notification_payload = _build_incoming_message_notification(
                            lead=lead,
                            lead_id=int(lead_id),
                            message_payload=payload,
                        )
                        notification = create_notification_event(
                            db,
                            event_type="incoming_message",
                            title=title,
                            body=body,
                            lead_id=int(lead_id),
                            payload=notification_payload,
                        )
                        db.commit()
                        publish_notification_payload(build_notification_payload(notification))

        async_jobs = summary.get("async_jobs") or []
        enqueued_task_ids: list[str] = []
        for job in async_jobs:
            job_type = job.get("type")
            if job_type == "send_automation_reply":
                async_result = celery_app.send_task(
                    "tasks.send_automation_reply",
                    kwargs={"igsid": str(job["igsid"])},
                )
                enqueued_task_ids.append(async_result.id)
            elif job_type == "post_meta_conversion_event":
                async_result = celery_app.send_task(
                    "tasks.post_meta_conversion_event",
                    kwargs={"event_id": int(job["event_id"])},
                )
                enqueued_task_ids.append(async_result.id)

        return {
            "status": "processed",
            "task": "process_webhook_event",
            "event_id": int(event_id),
            "jobs_enqueued": len(async_jobs),
            "task_ids": enqueued_task_ids,
        }
    except Exception as exc:
        db.rollback()
        current_retry = int(getattr(self.request, "retries", 0))
        retry_at = datetime.utcnow() + timedelta(seconds=min(300, 2 ** current_retry))
        try:
            failed_event = db.query(WebhookEvent).filter(WebhookEvent.id == int(event_id)).first()
            if failed_event:
                failed_event.processing_state = "failed"
                failed_event.enqueue_status = "failed"
                failed_event.last_error = str(exc)[:1000]
                failed_event.next_retry_at = retry_at
                db.commit()
        except Exception:
            db.rollback()
        log_event(
            logger,
            logging.ERROR,
            "task.process_webhook_event.error",
            event_id=int(event_id),
            error=str(exc),
        )
        return _retry_or_finalize(
            self,
            exc,
            "process_webhook_event",
            {"event_id": int(event_id)},
        )
    finally:
        db.close()


@celery_app.task(bind=True, base=BaseDBTask, name="tasks.send_automation_reply")
def send_automation_reply(self, *, igsid: str, message_text: Optional[str] = None) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        log_event(
            logger,
            logging.INFO,
            "task.send_automation_reply.attempt",
            igsid=str(igsid),
            has_custom_message=bool(message_text),
            custom_message_length=len(message_text or ""),
        )

        response = automation_mail(igsid, message_text=message_text)
        if response is None:
            raise RuntimeError(f"automation_mail failed for igsid={igsid}")

        log_event(
            logger,
            logging.INFO,
            "task.send_automation_reply.sent",
            igsid=str(igsid),
            message_id=response.get("message_id") if isinstance(response, dict) else None,
        )

        msg = append_chat_message(
            db,
            igsid=str(igsid),
            direction="outbound",
            message_text=message_text or os.getenv("IG_AUTOREPLY_TEXT"),
            platform_message_id=(response.get("message_id") if isinstance(response, dict) else None),
            payload=response if isinstance(response, dict) else None,
        )
        db.commit()

        lead = db.query(Lead).filter(Lead.contact_id == int(msg.contact_id)).first()
        if lead:
            message_payload = {
                "id": int(msg.id),
                "text": msg.text_cleaned or msg.text_raw or "📷 [Media/Attachment]",
                "direction": msg.direction,
                "time": msg.created_at.strftime("%I:%M %p") if msg.created_at else None,
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                "type": "new_message",
            }
            _publish_new_message(int(lead.id), message_payload)

        return {
            "status": "sent",
            "task": "send_automation_reply",
            "igsid": str(igsid),
            "response": response,
        }
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            logging.ERROR,
            "task.send_automation_reply.error",
            igsid=str(igsid),
            error=str(exc),
        )
        return _retry_or_finalize(
            self,
            exc,
            "send_automation_reply",
            {"igsid": str(igsid)},
        )
    finally:
        db.close()


@celery_app.task(bind=True, base=BaseDBTask, name="tasks.post_meta_conversion_event")
def post_meta_conversion_event(self, *, event_id: int) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        result = post_meta_event_by_id(db, event_id=event_id)
        if isinstance(result, dict) and result.get("status") != "skipped":
            status_code = result.get("status_code")
            if isinstance(status_code, int) and 200 <= status_code < 300:
                db.query(PaymentEvent).filter(
                    PaymentEvent.capi_event_id == str(event_id)
                ).update(
                    {PaymentEvent.capi_sent: True},
                    synchronize_session=False,
                )
                db.commit()
        task_status = result.get("status") if isinstance(result, dict) else None
        return {
            "status": task_status or "posted",
            "task": "post_meta_conversion_event",
            "event_id": int(event_id),
            "result": result,
        }
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "task.post_meta_conversion_event.error",
            event_id=int(event_id),
            error=str(exc),
        )
        return _retry_or_finalize(
            self,
            exc,
            "post_meta_conversion_event",
            {"event_id": int(event_id)},
        )
    finally:
        db.close()


@celery_app.task(bind=True, base=BaseDBTask, name="tasks.redrive_webhook_enqueue")
def redrive_webhook_enqueue(self, *, batch_size: int = 200) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        candidates = (
            db.query(WebhookEvent)
            .filter(
                WebhookEvent.event_type == "EVENT_RECEIVED",
                WebhookEvent.processed.is_(False),
                WebhookEvent.enqueue_status.in_(["pending", "failed"]),
                or_(WebhookEvent.next_retry_at.is_(None), WebhookEvent.next_retry_at <= now),
            )
            .order_by(WebhookEvent.created_at.asc(), WebhookEvent.id.asc())
            .limit(max(1, int(batch_size)))
            .all()
        )

        enqueued = 0
        failed = 0
        for event in candidates:
            attempt = int(event.enqueue_attempts or 0) + 1
            try:
                result = celery_app.send_task(
                    "tasks.process_webhook_event",
                    kwargs={"event_id": int(event.id)},
                )
                event.enqueue_status = "queued"
                event.enqueue_attempts = attempt
                event.queued_at = now
                event.next_retry_at = None
                event.last_error = None
                enqueued += 1
                log_event(
                    logger,
                    logging.INFO,
                    "webhook.redrive_enqueued",
                    event_id=int(event.id),
                    task_id=result.id,
                    enqueue_attempt=attempt,
                )
            except Exception as exc:
                backoff = min(300, 2 ** min(attempt, 8))
                event.enqueue_status = "failed"
                event.enqueue_attempts = attempt
                event.next_retry_at = now + timedelta(seconds=backoff)
                event.last_error = str(exc)[:1000]
                failed += 1
                log_event(
                    logger,
                    logging.ERROR,
                    "webhook.redrive_enqueue_failed",
                    event_id=int(event.id),
                    enqueue_attempt=attempt,
                    error=str(exc),
                )

        db.commit()
        return {
            "status": "completed",
            "task": "redrive_webhook_enqueue",
            "scanned": len(candidates),
            "enqueued": enqueued,
            "failed": failed,
        }
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            logging.ERROR,
            "task.redrive_webhook_enqueue.error",
            error=str(exc),
        )
        return _retry_or_finalize(
            self,
            exc,
            "redrive_webhook_enqueue",
            {"batch_size": int(batch_size)},
        )
    finally:
        db.close()


@celery_app.task(bind=True, base=BaseDBTask, name="tasks.persist_audit_log")
def persist_audit_log(
    self,
    *,
    action_type: str,
    actor_user_id: int | None = None,
    actor_username: str | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: str | None = None,
    payload: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        entry = ActivityLog(
            action_type=action_type,
            actor_user_id=actor_user_id,
            actor_username=actor_username,
            actor_role=actor_role,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details,
            payload=payload,
            created_at=datetime.utcnow(),
        )
        db.add(entry)
        db.commit()
        return {
            "status": "created",
            "task": "persist_audit_log",
            "audit_log_id": int(entry.id),
            "action_type": action_type,
        }
    except Exception as exc:
        db.rollback()
        log_event(
            logger,
            logging.ERROR,
            "task.persist_audit_log.error",
            action_type=action_type,
            error=str(exc),
        )
        return _retry_or_finalize(
            self,
            exc,
            "persist_audit_log",
            {"action_type": action_type},
        )
    finally:
        db.close()
