import hashlib
import json
import os
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

    # Persist the raw event and get a summary back
    persisted = persist_webhook_event(raw_body_text, data, db)
    status_tag = persisted.get("status_tag")

    # If this is an actual event for processing, handle business logic
    if status_tag == "EVENT_RECEIVED" and isinstance(data, dict):
        # Use the extracted orchestration helper to keep main.py thin
        handler_summary = handle_event_received(data, db)

    if status_tag == "BAD_JSON":
        return {"status": "BAD_JSON"}
    if status_tag == "EVENT_RECEIVED":
        return {"status": "EVENT_RECEIVED"}
    return {"status": "IGNORED"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )
