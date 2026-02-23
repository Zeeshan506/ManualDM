import os
from typing import Any, Dict, Optional

from celery import Task

from celery_app import celery_app
from database import SessionLocal
from models import WebhookEvent
from services.event_handlers import handle_event_received
from services.post_leads_to_meta import post_meta_event_by_id
from utils import append_chat_message, automation_mail


class BaseDBTask(Task):
	abstract = True


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

	print(f"❌ {task_name} failed after retries. details={details} error={exc}")
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
        task_status = result.get("status") if isinstance(result, dict) else None
        return {
            "status": task_status or "posted",
            "task": "post_meta_conversion_event",
            "event_id": int(event_id),
            "result": result,
        }
    except Exception as exc:
        return _retry_or_finalize(
            self,
            exc,
            "post_meta_conversion_event",
            {"event_id": int(event_id)},
        )
    finally:
        db.close()

