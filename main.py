import hashlib
import json
import os
from datetime import datetime

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

app = FastAPI()

# Configuration (Store these in environment variables later)
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "my_secret_token_123")
RAW_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./webhook_events.db")


def _with_sslmode(url: str) -> str:
    """Ensure Postgres connections use SSL when talking to Supabase."""
    if not url.startswith("postgresql"):
        return url
    if "sslmode=" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}sslmode=require"


DATABASE_URL = _with_sslmode(RAW_DATABASE_URL)

# SQLAlchemy setup
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    object = Column(String, nullable=True)
    status = Column(String, nullable=False)
    raw_payload = Column(JSON, nullable=True)
    raw_body = Column(Text, nullable=True)
    fingerprint = Column(String, nullable=True, index=True)


Base.metadata.create_all(bind=engine)


def ensure_schema():
    """SQLite-only backfill for older local databases."""
    if engine.url.get_backend_name() != "sqlite":
        return
    with engine.connect() as conn:
        existing_cols = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info('webhook_events')").fetchall()
        }
        if "fingerprint" not in existing_cols:
            conn.exec_driver_sql("ALTER TABLE webhook_events ADD COLUMN fingerprint TEXT")
        conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_webhook_events_fingerprint ON webhook_events(fingerprint)"
        )


ensure_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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


VOLATILE_KEYS = {"timestamp", "time", "sent_time", "created_time", "sent_at"}


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def compute_fingerprint(payload):
    if not isinstance(payload, (dict, list)):
        return None
    try:
        stable = _strip_volatile(payload)
        serialized = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    except Exception:
        return None


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

    if status_tag == "BAD_JSON":
        return {"status": "BAD_JSON"}
    if status_tag == "EVENT_RECEIVED":
        return {"status": "EVENT_RECEIVED"}
    return {"status": "IGNORED"}


if __name__ == "__main__":
    uvicorn.run(
        "main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=True
    )
