import os
import re
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import InboundMessage, Lead


router = APIRouter()

IG_USER_ID = os.getenv("IG_USER_ID")
META_PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN")


def now_utc():
    return datetime.now(timezone.utc)


def normalize_phone(phone_raw):
    if not phone_raw:
        return None
    compact = re.sub(r"[\s\-()]+", "", phone_raw.strip())
    if not compact.startswith("+"):
        return None
    digits = compact[1:]
    if not digits.isdigit():
        return None
    if len(digits) < 8 or len(digits) > 15:
        return None
    return f"+{digits}"


def clean_text(text):
    if not text:
        return ""
    return " ".join(text.strip().split())


def compute_dedup_key(phone=None, email=None):
    parts = []
    if phone:
        normalized = normalize_phone(phone)
        if normalized:
            parts.append(normalized)
    if email:
        parts.append(email.strip().lower())
    return "|".join(parts) if parts else None


def upsert_lead_by_instagram_user(db: Session, instagram_user_id: str) -> Lead:
    lead = db.query(Lead).filter(Lead.instagram_user_id == instagram_user_id).first()
    if lead:
        return lead

    lead = Lead(
        instagram_user_id=instagram_user_id,
        flow_step="new",
        status="new",
        created_at=now_utc(),
        updated_at=now_utc(),
    )
    db.add(lead)
    db.flush()
    return lead


def send_message_to_meta(recipient_ig_id, text):
    if not IG_USER_ID or not META_PAGE_ACCESS_TOKEN:
        return None

    url = f"https://graph.facebook.com/v17.0/{IG_USER_ID}/messages"
    payload = {
        "recipient": {"id": recipient_ig_id},
        "message": {"text": text},
    }
    params = {"access_token": META_PAGE_ACCESS_TOKEN}
    return requests.post(url, json=payload, params=params, timeout=10)


def _extract_message_fields(payload):
    entry = payload.get("entry", [])
    if not entry:
        raise ValueError("Missing entry")

    first_entry = entry[0]

    if first_entry.get("messaging"):
        event = first_entry["messaging"][0]
        instagram_user_id = event.get("sender", {}).get("id")
        inbound_text = event.get("message", {}).get("text", "")
        platform_message_id = event.get("message", {}).get("mid")
    elif first_entry.get("changes"):
        change_value = first_entry["changes"][0].get("value", {})
        message = (change_value.get("messages") or [{}])[0]
        contact = (change_value.get("contacts") or [{}])[0]
        instagram_user_id = message.get("from") or contact.get("wa_id")
        inbound_text = message.get("text", {}).get("body", "")
        platform_message_id = message.get("id")
    else:
        raise ValueError("Unsupported webhook payload shape")

    if not instagram_user_id:
        raise ValueError("Missing instagram user id")

    return instagram_user_id, inbound_text, platform_message_id


def decide_and_respond(lead_id: int, inbound_text: str):
    db = SessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.id == lead_id).first()
        if not lead:
            return

        step = lead.flow_step or "new"
        instagram_user_id = lead.instagram_user_id
        cleaned = clean_text(inbound_text)

        if step == "new":
            text = "Hi! Thanks for messaging us. Can I have your full name?"
            send_message_to_meta(instagram_user_id, text)
            lead.flow_step = "asking_name"
        elif step == "asking_name":
            if len(cleaned) >= 2:
                lead.name = cleaned
                lead.flow_step = "asking_phone"
                text = f"Thanks {cleaned}. Could you share your phone number so we can reach you?"
            else:
                text = "Sorry, I didn't catch your name. Please enter your full name."
            send_message_to_meta(instagram_user_id, text)
        elif step == "asking_phone":
            normalized = normalize_phone(cleaned)
            if normalized:
                lead.phone = normalized
                lead.dedup_key = compute_dedup_key(phone=normalized, email=lead.email)
                lead.flow_step = "complete"
                lead.status = "complete"
                text = "Thanks — we've saved your details. Someone will follow up soon."
            else:
                text = "That phone number doesn't look right. Please send it in international format, e.g. +1xxxxxxxxxx"
            send_message_to_meta(instagram_user_id, text)
        else:
            text = "Thanks — we'll contact you soon."
            send_message_to_meta(instagram_user_id, text)

        lead.updated_at = now_utc()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/webhook/meta")
async def meta_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    payload = await request.json()

    try:
        instagram_user_id, inbound_text, platform_message_id = _extract_message_fields(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cleaned = clean_text(inbound_text)
    received_at = now_utc()

    try:
        lead = upsert_lead_by_instagram_user(db, instagram_user_id)
        lead.last_message_at = received_at
        lead.updated_at = received_at

        inbound = InboundMessage(
            lead_id=lead.id,
            instagram_user_id=instagram_user_id,
            platform_message_id=platform_message_id,
            text_raw=inbound_text,
            text_cleaned=cleaned,
            payload=payload,
            processed=True,
            received_at=received_at,
            created_at=received_at,
        )
        db.add(inbound)
        db.commit()
    except Exception:
        db.rollback()
        raise

    background_tasks.add_task(decide_and_respond, lead.id, cleaned)
    return {"status": "accepted", "lead_id": lead.id}