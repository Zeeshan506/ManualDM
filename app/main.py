import json
import os
import re
import logging
import time
from uuid import uuid4
from datetime import datetime
from app.services.webhook_events import persist_webhook_event
import uvicorn
from anyio import to_thread
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.celery_app import celery_app
from app.core.database import SessionLocal, get_db, init_db
from app.core.logging import configure_logging, get_logger, log_event, reset_request_id, set_request_id
from app.db.models import Lead
from app.services.meta_conversion_events import (
    persist_custom_event_for_lead,
    persist_leadsubmitted_event_for_lead,
    persist_purchase_event_for_lead,
)
from app.services.activity_logs import enqueue_activity_log
from app.api.routes.api import router as api_router
from app.api.routes.auth import router as auth_router
from app.api.routes.admin import router as admin_router
from app.api.routes.users import router as users_router


configure_logging()
logger = get_logger(__name__)


app = FastAPI()

frontend_url = (os.getenv("FRONTEND_URL") or "").strip().rstrip("/")
allowed_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
if frontend_url:
    allowed_origins.append(frontend_url)

# --- Add the CORS Configuration Here ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],  # Allows all headers
)
# Include API routes
app.include_router(api_router)
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(admin_router)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid4())
    token = set_request_id(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log_event(
            logger,
            logging.ERROR,
            "request.unhandled_exception",
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        reset_request_id(token)
        raise

    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["x-request-id"] = request_id
    log_event(
        logger,
        logging.INFO,
        "request.completed",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    reset_request_id(token)
    return response

# Configuration
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_secret_token_123")


def _enqueue_webhook_processing(event_id: int) -> None:
    result = celery_app.send_task(
        "tasks.process_webhook_event",
        kwargs={"event_id": int(event_id)},
    )
    log_event(
        logger,
        logging.INFO,
        "webhook.enqueued",
        event_id=int(event_id),
        task_id=result.id,
        task_name="tasks.process_webhook_event",
    )


def _persist_webhook_event_threadsafe(raw_body_text: str, data: dict | None) -> tuple[str, int | None]:
    db = SessionLocal()
    try:
        persisted = persist_webhook_event(raw_body_text, data, db)
        db.commit()
        return persisted.get("status_tag"), persisted.get("event_id")
    except Exception:
        db.rollback()
        log_event(
            logger,
            logging.ERROR,
            "webhook.persist_failed",
            payload_size=len(raw_body_text),
            payload_type=type(data).__name__ if data is not None else None,
        )
        raise
    finally:
        db.close()


class LeadContactUpdatePayload(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None


class LeadMetaEventPayload(BaseModel):
    event_name: str
    event_time: int | None = None
    action_source: str = "business_messaging"
    messaging_channel: str = "instagram"
    user_data: dict | None = None
    custom_data: dict | None = None
    partner_agent: str | None = None
    send_now: bool = True



class MockPurchasePayload(BaseModel):
    value: float
    currency: str = "USD"
    send_now: bool = True


def _normalize_email(email: str | None) -> str | None:
    if not email:
        return None
    normalized = email.strip().lower()
    return normalized or None


def _normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    compact = re.sub(r"[\s\-()]+", "", phone.strip())
    if compact.startswith("+"):
        digits = compact[1:]
        if not digits.isdigit() or len(digits) < 8 or len(digits) > 15:
            return None
        return f"+{digits}"

    if not compact.isdigit() or len(compact) < 8 or len(compact) > 15:
        return None
    return f"+{compact}"


def _normalize_name(name: str | None) -> str | None:
    if name is None:
        return None
    normalized = " ".join(name.strip().split())
    return normalized or None

# Initialize database (creates tables in dev/sqlite; safe for Postgres if already migrated)
init_db()



@app.get("/webhook")
def verify_webhook(request: Request):
    """
    Handles the Webhook Verification Challenge from Meta.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            log_event(logger, logging.INFO, "webhook.verify_success")
            return int(challenge)
        else:
            log_event(logger, logging.WARNING, "webhook.verify_token_mismatch")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Verification token mismatch",
            )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Missing parameters"
    )



@app.post("/webhook")
async def receive_message(request: Request):

    raw_body_bytes = await request.body()
    raw_body_text = raw_body_bytes.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw_body_text)
        status_tag = "EVENT_RECEIVED" if isinstance(data, dict) and data.get("object") == "instagram" else "IGNORED"
    except Exception:
        data = None
        status_tag = "BAD_JSON"
        log_event(
            logger,
            logging.WARNING,
            "webhook.bad_json",
            payload_size=len(raw_body_text),
            preview=raw_body_text[:300],
        )

    log_event(
        logger,
        logging.INFO,
        "webhook.received",
        timestamp=datetime.utcnow().isoformat(),
        status_tag=status_tag,
        payload_size=len(raw_body_text),
        source=(data.get("object") if isinstance(data, dict) else None),
    )

    status_tag, persisted_event_id = await to_thread.run_sync(
        _persist_webhook_event_threadsafe,
        raw_body_text,
        data if isinstance(data, dict) else None,
    )

    log_event(
        logger,
        logging.INFO,
        "webhook.persisted",
        status_tag=status_tag,
        persisted_event_id=persisted_event_id,
    )

    if status_tag == "EVENT_RECEIVED" and isinstance(data, dict) and persisted_event_id:
        try:
            await to_thread.run_sync(_enqueue_webhook_processing, int(persisted_event_id))
        except Exception as exc:
            # Do not fail webhook response if queueing fails.
            log_event(
                logger,
                logging.ERROR,
                "webhook.enqueue_failed",
                persisted_event_id=persisted_event_id,
                error=str(exc),
            )

    if status_tag == "BAD_JSON":
        return {"status": "BAD_JSON"}
    if status_tag == "EVENT_RECEIVED":
        return {"status": "EVENT_RECEIVED"}
    return {"status": "IGNORED"}


@app.post("/leads/{lead_id}/contact-details")
def update_lead_contact_details(
    lead_id: int,
    body: LeadContactUpdatePayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if body.name is None and body.email is None and body.phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of name, email, or phone must be provided",
        )

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    normalized_email = _normalize_email(body.email)
    normalized_phone = _normalize_phone(body.phone)
    normalized_name = _normalize_name(body.name)

    if body.name is not None and len((normalized_name or "")) > 120:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid name value; max length is 120 characters",
        )

    if body.email is not None and not normalized_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email value",
        )

    if body.phone is not None and not normalized_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid phone value; expected 8-15 digits with optional leading +",
        )

    try:
        if body.name is not None:
            lead.name = normalized_name
        if body.email is not None:
            lead.email = normalized_email
        if body.phone is not None:
            lead.phone = normalized_phone

        queued_task_id = None
        leadsubmitted_event_id = None
        contact_fields_updated = body.email is not None or body.phone is not None
        if contact_fields_updated and lead.email and lead.phone:
            leadsubmitted_event = persist_leadsubmitted_event_for_lead(
                db,
                lead_id=int(lead.id),
                email=lead.email,
                phone=lead.phone,
                mock_invoice_id=f"mock-invoice-{lead.id}",
            )
            if leadsubmitted_event:
                leadsubmitted_event_id = int(leadsubmitted_event.id)

        db.commit()
        db.refresh(lead)

        if leadsubmitted_event_id is not None:
            task = celery_app.send_task(
                "tasks.post_meta_conversion_event",
                kwargs={"event_id": int(leadsubmitted_event_id)},
            )
            queued_task_id = task.id
    except Exception:
        db.rollback()
        raise

    enqueue_activity_log(
        background_tasks,
        actor="system",
        action="UPDATE_LEAD_CONTACT",
        details=f"Updated contact details for lead #{lead.id}",
        lead_id=lead.id,
        metadata={
            "name_updated": body.name is not None,
            "email_updated": body.email is not None,
            "phone_updated": body.phone is not None,
            "leadsubmitted_event_id": leadsubmitted_event_id,
        },
    )

    return {
        "status": "updated",
        "lead_id": lead.id,
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "leadsubmitted_event_id": leadsubmitted_event_id,
        "leadsubmitted_queued_task_id": queued_task_id,
    }


@app.post("/leads/{lead_id}/mock-purchase")
def create_mock_purchase_event(
    lead_id: int,
    body: MockPurchasePayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):


    if body.value <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="value must be greater than 0",
        )

    currency = (body.currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="currency must be a 3-letter ISO code",
        )

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    try:
        event = persist_purchase_event_for_lead(
            db,
            lead_id=lead_id,
            value=float(body.value),
            currency=currency,
            email=lead.email,
            phone=lead.phone,
        )
        db.commit()
        db.refresh(event)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        db.rollback()
        raise

    task_id = None
    if body.send_now:
        task = celery_app.send_task(
            "tasks.post_meta_conversion_event",
            kwargs={"event_id": int(event.id)},
        )
        task_id = task.id

    enqueue_activity_log(
        background_tasks,
        actor="system",
        action="CREATE_MOCK_PURCHASE_EVENT",
        details=f"Created mock purchase for lead #{lead_id}",
        lead_id=lead_id,
        metadata={"meta_event_id": int(event.id), "value": body.value, "currency": currency, "queued_for_meta": bool(body.send_now)},
    )

    return {
        "status": "created",
        "lead_id": lead_id,
        "meta_event_id": int(event.id),
        "event_name": event.event_name,
        "queued_for_meta": bool(body.send_now),
        "task_id": task_id,
    }


@app.post("/leads/{lead_id}/meta-events")
def create_lead_meta_event(
    lead_id: int,
    body: LeadMetaEventPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        event = persist_custom_event_for_lead(
            db,
            lead_id=lead_id,
            event_name=body.event_name,
            event_time=body.event_time,
            action_source=body.action_source,
            messaging_channel=body.messaging_channel,
            user_data=body.user_data,
            custom_data=body.custom_data,
            partner_agent=body.partner_agent,
        )
        db.commit()
        db.refresh(event)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        db.rollback()
        raise

    task_id = None
    if body.send_now:
        task = celery_app.send_task(
            "tasks.post_meta_conversion_event",
            kwargs={"event_id": int(event.id)},
        )
        task_id = task.id

    enqueue_activity_log(
        background_tasks,
        actor="system",
        action="CREATE_META_EVENT",
        details=f"Created meta event {event.event_name} for lead #{lead_id}",
        lead_id=lead_id,
        metadata={"meta_event_id": int(event.id), "event_name": event.event_name, "queued_for_meta": bool(body.send_now)},
    )

    return {
        "status": "created",
        "lead_id": lead_id,
        "meta_event_id": event.id,
        "event_name": event.event_name,
        "queued_for_meta": bool(body.send_now),
        "task_id": task_id,
    }


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )
