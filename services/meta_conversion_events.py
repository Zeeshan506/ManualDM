import os
import re
import hashlib
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from models import Lead, MetaConversionEvent


IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID") or os.getenv("IG_USER_ID")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    normalized = email.strip().lower()
    return normalized or None


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None

    compact = re.sub(r"[\s\-()]+", "", phone.strip())
    if compact.startswith("+"):
        digits = compact[1:]
    else:
        digits = compact

    if not digits.isdigit():
        return None
    if len(digits) < 8 or len(digits) > 15:
        return None
    return digits


def _build_viewcontent_payload(*, lead: Lead, igsid: str) -> Dict[str, Any]:
    event_time = int(datetime.utcnow().timestamp())

    user_data: Dict[str, Any] = {"ig_sid": str(igsid)}
    if IG_BUSINESS_ACCOUNT_ID:
        user_data["instagram_business_account_id"] = str(IG_BUSINESS_ACCOUNT_ID)

    event: Dict[str, Any] = {
        "event_name": "ViewContent",
        "event_time": event_time,
        "action_source": "business_messaging",
        "messaging_channel": "instagram",
        "user_data": user_data,
        "custom_data": {
            "lead_id": lead.id,
            "lead_status": lead.status,
        },
    }

    return {"data": [event]}


def _build_ordercreated_payload(
    *,
    lead: Lead,
    igsid: str,
    hashed_email: Optional[str],
    hashed_phone: Optional[str],
    value: Optional[float] = None,
    currency: Optional[str] = None,
) -> Dict[str, Any]:
    event_time = int(datetime.utcnow().timestamp())

    user_data: Dict[str, Any] = {"ig_sid": str(igsid)}
    if IG_BUSINESS_ACCOUNT_ID:
        user_data["instagram_business_account_id"] = str(IG_BUSINESS_ACCOUNT_ID)
    if hashed_email:
        user_data["em"] = hashed_email
    if hashed_phone:
        user_data["ph"] = hashed_phone

    custom_data: Dict[str, Any] = {
        "lead_id": lead.id,
        "lead_status": lead.status,
    }
    if value is not None:
        custom_data["value"] = value
    if currency:
        custom_data["currency"] = currency.upper()

    event: Dict[str, Any] = {
        "event_name": "OrderCreated",
        "event_time": event_time,
        "action_source": "business_messaging",
        "messaging_channel": "instagram",
        "user_data": user_data,
        "custom_data": custom_data,
    }

    return {"data": [event]}


def persist_viewcontent_event_for_lead(db: Session, *, lead_id: int, igsid: str) -> Optional[MetaConversionEvent]:
    """
    Persist a single ViewContent event for a newly-created lead.

    Idempotency behavior:
      - If a ViewContent event already exists for this lead, skip and return None.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    existing = (
        db.query(MetaConversionEvent)
        .filter(MetaConversionEvent.lead_id == lead_id, MetaConversionEvent.event_name == "ViewContent")
        .first()
    )
    if existing:
        return None

    payload = _build_viewcontent_payload(lead=lead, igsid=igsid)
    event_data = payload["data"][0]

    record = MetaConversionEvent(
        lead_id=lead.id,
        event_name=event_data["event_name"],
        event_time=event_data["event_time"],
        action_source=event_data.get("action_source"),
        messaging_channel=event_data.get("messaging_channel"),
        user_data=event_data.get("user_data"),
        custom_data=event_data.get("custom_data"),
        partner_agent=None,
        full_payload=payload,
        created_at=datetime.utcnow(),
    )

    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def persist_ordercreated_event_for_lead(
    db: Session,
    *,
    lead_id: int,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    value: Optional[float] = None,
    currency: Optional[str] = None,
) -> Optional[MetaConversionEvent]:
    """
    Persist a single OrderCreated event for a lead after contact enrichment.

    Idempotency behavior:
      - If an OrderCreated event already exists for this lead, skip and return None.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    existing = (
        db.query(MetaConversionEvent)
        .filter(MetaConversionEvent.lead_id == lead_id, MetaConversionEvent.event_name == "OrderCreated")
        .first()
    )
    if existing:
        return None

    normalized_email = _normalize_email(email if email is not None else lead.email)
    normalized_phone = _normalize_phone(phone if phone is not None else lead.phone)

    hashed_email = _sha256(normalized_email) if normalized_email else None
    hashed_phone = _sha256(normalized_phone) if normalized_phone else None

    contact = lead.contact
    igsid = contact.igsid if contact and contact.igsid else None
    if not igsid:
        raise ValueError(f"Lead {lead_id} has no related contact IGSID")

    payload = _build_ordercreated_payload(
        lead=lead,
        igsid=str(igsid),
        hashed_email=hashed_email,
        hashed_phone=hashed_phone,
        value=value,
        currency=currency,
    )
    event_data = payload["data"][0]

    record = MetaConversionEvent(
        lead_id=lead.id,
        event_name=event_data["event_name"],
        event_time=event_data["event_time"],
        action_source=event_data.get("action_source"),
        messaging_channel=event_data.get("messaging_channel"),
        user_data=event_data.get("user_data"),
        custom_data=event_data.get("custom_data"),
        partner_agent=None,
        full_payload=payload,
        created_at=datetime.utcnow(),
    )

    db.add(record)
    db.commit()
    db.refresh(record)
    return record
