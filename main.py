import hashlib
import json
import os
import re
from datetime import datetime
from utils import (
    automation_mail,
    upsert_lead_from_payload,
    track_inbound_chat_history,
    append_chat_message,
)
from services.webhook_events import persist_webhook_event
from services.event_handlers import handle_event_received
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import Lead, WebhookEvent


app = FastAPI()

# Configuration
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_secret_token_123")


class LeadContactUpdatePayload(BaseModel):
    email: str | None = None
    phone: str | None = None


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

# Initialize database (creates tables in dev/sqlite; safe for Postgres if already migrated)
init_db()



@app.get("/webhook")
async def verify_webhook(request: Request):
    """
    Handles the Webhook Verification Challenge from Meta.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return int(challenge)
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Verification token mismatch",
            )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Missing parameters"
    )



@app.post("/webhook")
async def receive_message(request: Request, db: Session = Depends(get_db)):

    raw_body_bytes = await request.body()
    raw_body_text = raw_body_bytes.decode("utf-8", errors="replace")

    try:
        data = json.loads(raw_body_text)
        status_tag = "EVENT_RECEIVED" if isinstance(data, dict) and data.get("object") == "instagram" else "IGNORED"
    except Exception:
        data = None
        status_tag = "BAD_JSON"
        print("⚠️ Failed to parse JSON")
        print(raw_body_text)

    print("\n================ WEBHOOK EVENT ================")
    print("Timestamp:", datetime.utcnow().isoformat())
    if data is not None:
        print(json.dumps(data, indent=2))
    else:
        print(raw_body_text)
    print("==============================================\n")

    persisted = persist_webhook_event(raw_body_text, data, db)
    status_tag = persisted.get("status_tag")

    if status_tag == "EVENT_RECEIVED" and isinstance(data, dict):
        handler_summary = handle_event_received(data, db)

    if status_tag == "BAD_JSON":
        return {"status": "BAD_JSON"}
    if status_tag == "EVENT_RECEIVED":
        return {"status": "EVENT_RECEIVED"}
    return {"status": "IGNORED"}


@app.post("/leads/{lead_id}/contact-details")
async def update_lead_contact_details(
    lead_id: int,
    body: LeadContactUpdatePayload,
    db: Session = Depends(get_db),
):
    if body.email is None and body.phone is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of email or phone must be provided",
        )

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    normalized_email = _normalize_email(body.email)
    normalized_phone = _normalize_phone(body.phone)

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
        if body.email is not None:
            lead.email = normalized_email
        if body.phone is not None:
            lead.phone = normalized_phone
        db.commit()
        db.refresh(lead)
    except Exception:
        db.rollback()
        raise

    return {
        "status": "updated",
        "lead_id": lead.id,
        "email": lead.email,
        "phone": lead.phone,
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )
