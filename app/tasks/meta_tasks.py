import os
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from celery import Task

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.logging import get_logger, log_event
from app.db.models import ActivityLog, PaymentEvent, WebhookEvent
from app.services.event_handlers import handle_event_received
from app.services.post_leads_to_meta import post_meta_event_by_id
from utils import append_chat_message, automation_mail


class BaseDBTask(Task):
    abstract = True


logger = get_logger(__name__)


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
            return {
                "status": "skipped",
                "task": "process_webhook_event",
                "event_id": int(event_id),
                "reason": "invalid_payload",
            }

        summary = handle_event_received(event.payload, db)
        db.commit()

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
        response = automation_mail(igsid, message_text=message_text)
        if response is None:
            raise RuntimeError(f"automation_mail failed for igsid={igsid}")

        append_chat_message(
            db,
            igsid=str(igsid),
            direction="outbound",
            message_text=message_text or os.getenv("IG_AUTOREPLY_TEXT"),
            platform_message_id=(response.get("message_id") if isinstance(response, dict) else None),
            payload=response if isinstance(response, dict) else None,
        )
        db.commit()

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
