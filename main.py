import hashlib
import json
import os
from datetime import datetime
from utils import compute_fingerprint, _strip_volatile, automation_mail, _extract_inbound_sender_id
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import WebhookEvent


app = FastAPI()

# Configuration
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_secret_token_123")

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
            # Return the challenge string as a plain integer/text, not JSON
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

    # 🔥 FULL RAW LOG
    print("\n================ WEBHOOK EVENT ================")
    print("Timestamp:", datetime.utcnow().isoformat())
    if data is not None:
        print(json.dumps(data, indent=2))
    else:
        print(raw_body_text)
    print("==============================================\n")

    fingerprint = compute_fingerprint(data) if data is not None else None

    # Try to merge with an existing event that looks identical after stripping volatile fields
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
    else:
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
        except Exception:
            db.rollback()
            raise

    if status_tag == "EVENT_RECEIVED" and isinstance(data, dict):
        sender_id = _extract_inbound_sender_id(data)
        if sender_id:
            try:
                result = automation_mail(sender_id)
                print(f"Automation mail result for PSID {sender_id}: {result}")
            except Exception as exc:
                print(f"⚠️ Failed to send automated message: {exc}")

    if status_tag == "BAD_JSON":
        return {"status": "BAD_JSON"}
    if status_tag == "EVENT_RECEIVED":
        return {"status": "EVENT_RECEIVED"}
    return {"status": "IGNORED"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )
