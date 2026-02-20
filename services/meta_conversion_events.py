import os
import re
import hashlib
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from models import Lead, MetaConversionEvent


IG_BUSINESS_ACCOUNT_ID = (
    os.getenv("IG_ACCOUNT_ID")
    or os.getenv("INSTAGRAM_ACCOUNT_ID")
    or os.getenv("IG_USER_ID")
)


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


def _create_event_id() -> str:
    return str(uuid.uuid4())


def _build_base_event(*, event_name: str, event_time: Optional[int] = None) -> Dict[str, Any]:
    resolved_event_time = int(event_time) if event_time is not None else int(datetime.utcnow().timestamp())
    return {
        "event_name": event_name,
        "event_time": resolved_event_time,
        "event_id": _create_event_id(),
        "action_source": "business_messaging",
        "messaging_channel": "instagram",
    }


def _build_contact_payload(*, lead: Lead, igsid: str, event_time: Optional[int] = None) -> Dict[str, Any]:
    event: Dict[str, Any] = _build_base_event(event_name="ViewContent", event_time=event_time)

    user_data: Dict[str, Any] = {
        "ig_sid": str(igsid),
    }
    if IG_BUSINESS_ACCOUNT_ID:
        user_data["ig_account_id"] = str(IG_BUSINESS_ACCOUNT_ID)

    event["user_data"] = user_data

    return {"data": [event]}


def _build_leadsubmitted_payload(
    *,
    lead: Lead,
    igsid: str,
    hashed_email: str,
    hashed_phone: str,
    mock_invoice_id: Optional[str] = None,
    event_time: Optional[int] = None,
) -> Dict[str, Any]:
    event: Dict[str, Any] = _build_base_event(event_name="LeadSubmitted", event_time=event_time)

    user_data: Dict[str, Any] = {
        "ig_sid": str(igsid),
        "em": hashed_email,
        "ph": hashed_phone,
    }
    if IG_BUSINESS_ACCOUNT_ID:
        user_data["ig_account_id"] = str(IG_BUSINESS_ACCOUNT_ID)

    event["user_data"] = user_data
    event["custom_data"] = {
        "mock_invoice_generated": True,
        "mock_invoice_id": mock_invoice_id or f"mock-invoice-{lead.id}-{int(datetime.utcnow().timestamp())}",
    }

    return {"data": [event]}


def _build_purchase_payload(
    *,
    lead: Lead,
    igsid: str,
    hashed_email: Optional[str],
    hashed_phone: Optional[str],
    value: float,
    currency: str,
    event_time: Optional[int] = None,
) -> Dict[str, Any]:
    event: Dict[str, Any] = _build_base_event(event_name="Purchase", event_time=event_time)

    user_data: Dict[str, Any] = {
        "ig_sid": str(igsid),
    }
    if IG_BUSINESS_ACCOUNT_ID:
        user_data["ig_account_id"] = str(IG_BUSINESS_ACCOUNT_ID)
    if hashed_email:
        user_data["em"] = hashed_email
    if hashed_phone:
        user_data["ph"] = hashed_phone

    event["user_data"] = user_data
    event["custom_data"] = {
        "value": float(value),
        "currency": str(currency).upper(),
    }

    return {"data": [event]}


def _build_custom_payload(
    *,
    lead: Lead,
    event_name: str,
    event_time: Optional[int] = None,
    action_source: str = "business_messaging",
    messaging_channel: str = "instagram",
    user_data: Optional[Dict[str, Any]] = None,
    custom_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved_event_time = int(event_time) if event_time is not None else int(datetime.utcnow().timestamp())

    merged_user_data: Dict[str, Any] = {}
    lead_igsid = lead.contact.igsid if lead.contact and lead.contact.igsid else None
    if lead_igsid:
        merged_user_data["ig_sid"] = str(lead_igsid)
    if IG_BUSINESS_ACCOUNT_ID:
        merged_user_data["ig_account_id"] = str(IG_BUSINESS_ACCOUNT_ID)

    normalized_email = _normalize_email(lead.email)
    normalized_phone = _normalize_phone(lead.phone)
    if normalized_email:
        merged_user_data["em"] = _sha256(normalized_email)
    if normalized_phone:
        merged_user_data["ph"] = _sha256(normalized_phone)

    if user_data:
        merged_user_data.update(user_data)

    merged_custom_data: Dict[str, Any] = {
        "lead_id": lead.id,
        "lead_status": lead.status,
    }
    if custom_data:
        merged_custom_data.update(custom_data)

    event: Dict[str, Any] = {
        "event_name": event_name,
        "event_time": resolved_event_time,
        "event_id": _create_event_id(),
        "action_source": action_source,
        "messaging_channel": messaging_channel,
        "user_data": merged_user_data,
        "custom_data": merged_custom_data,
    }

    return {"data": [event]}


def persist_contact_event_for_lead(
    db: Session,
    *,
    lead_id: int,
    igsid: str,
    event_time: Optional[int] = None,
) -> MetaConversionEvent:
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    payload = _build_contact_payload(lead=lead, igsid=igsid, event_time=event_time)
    event_data = payload["data"][0]

    record = MetaConversionEvent(
        lead_id=lead.id,
        event_name=event_data["event_name"],
        event_time=event_data["event_time"],
        event_id=event_data.get("event_id"),
        action_source=event_data.get("action_source"),
        messaging_channel=event_data.get("messaging_channel"),
        user_data=event_data.get("user_data"),
        custom_data=event_data.get("custom_data"),
        partner_agent=None,
        full_payload=payload,
        created_at=datetime.utcnow(),
    )

    db.add(record)
    db.flush()
    return record


def persist_leadsubmitted_event_for_lead(
    db: Session,
    *,
    lead_id: int,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    mock_invoice_id: Optional[str] = None,
    event_time: Optional[int] = None,
) -> Optional[MetaConversionEvent]:
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    existing = (
        db.query(MetaConversionEvent)
        .filter(MetaConversionEvent.lead_id == lead_id, MetaConversionEvent.event_name == "LeadSubmitted")
        .first()
    )
    if existing:
        return None

    normalized_email = _normalize_email(email if email is not None else lead.email)
    normalized_phone = _normalize_phone(phone if phone is not None else lead.phone)
    if not normalized_email or not normalized_phone:
        raise ValueError("LeadSubmitted requires both email and phone")

    hashed_email = _sha256(normalized_email)
    hashed_phone = _sha256(normalized_phone)

    contact = lead.contact
    igsid = contact.igsid if contact and contact.igsid else None
    if not igsid:
        raise ValueError(f"Lead {lead_id} has no related contact IGSID")

    payload = _build_leadsubmitted_payload(
        lead=lead,
        igsid=str(igsid),
        hashed_email=hashed_email,
        hashed_phone=hashed_phone,
        mock_invoice_id=mock_invoice_id,
        event_time=event_time,
    )
    event_data = payload["data"][0]

    record = MetaConversionEvent(
        lead_id=lead.id,
        event_name=event_data["event_name"],
        event_time=event_data["event_time"],
        event_id=event_data.get("event_id"),
        action_source=event_data.get("action_source"),
        messaging_channel=event_data.get("messaging_channel"),
        user_data=event_data.get("user_data"),
        custom_data=event_data.get("custom_data"),
        partner_agent=None,
        full_payload=payload,
        created_at=datetime.utcnow(),
    )

    db.add(record)
    db.flush()
    return record


def persist_purchase_event_for_lead(
    db: Session,
    *,
    lead_id: int,
    value: float,
    currency: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    event_time: Optional[int] = None,
) -> MetaConversionEvent:
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    contact = lead.contact
    igsid = contact.igsid if contact and contact.igsid else None
    if not igsid:
        raise ValueError(f"Lead {lead_id} has no related contact IGSID")

    normalized_email = _normalize_email(email if email is not None else lead.email)
    normalized_phone = _normalize_phone(phone if phone is not None else lead.phone)
    hashed_email = _sha256(normalized_email) if normalized_email else None
    hashed_phone = _sha256(normalized_phone) if normalized_phone else None

    payload = _build_purchase_payload(
        lead=lead,
        igsid=str(igsid),
        hashed_email=hashed_email,
        hashed_phone=hashed_phone,
        value=float(value),
        currency=currency,
        event_time=event_time,
    )
    event_data = payload["data"][0]

    record = MetaConversionEvent(
        lead_id=lead.id,
        event_name=event_data["event_name"],
        event_time=event_data["event_time"],
        event_id=event_data.get("event_id"),
        action_source=event_data.get("action_source"),
        messaging_channel=event_data.get("messaging_channel"),
        user_data=event_data.get("user_data"),
        custom_data=event_data.get("custom_data"),
        partner_agent=None,
        full_payload=payload,
        created_at=datetime.utcnow(),
    )

    db.add(record)
    db.flush()
    return record


def persist_custom_event_for_lead(
    db: Session,
    *,
    lead_id: int,
    event_name: str,
    event_time: Optional[int] = None,
    action_source: str = "business_messaging",
    messaging_channel: str = "instagram",
    user_data: Optional[Dict[str, Any]] = None,
    custom_data: Optional[Dict[str, Any]] = None,
    partner_agent: Optional[str] = None,
) -> MetaConversionEvent:
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    normalized_event_name = (event_name or "").strip()
    if not normalized_event_name:
        raise ValueError("event_name is required")

    payload = _build_custom_payload(
        lead=lead,
        event_name=normalized_event_name,
        event_time=event_time,
        action_source=action_source,
        messaging_channel=messaging_channel,
        user_data=user_data,
        custom_data=custom_data,
    )
    event_data = payload["data"][0]

    record = MetaConversionEvent(
        lead_id=lead.id,
        event_name=event_data["event_name"],
        event_time=event_data["event_time"],
        event_id=event_data.get("event_id"),
        action_source=event_data.get("action_source"),
        messaging_channel=event_data.get("messaging_channel"),
        user_data=event_data.get("user_data"),
        custom_data=event_data.get("custom_data"),
        partner_agent=partner_agent,
        full_payload=payload,
        created_at=datetime.utcnow(),
    )

    db.add(record)
    db.flush()
    return record
