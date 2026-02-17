from datetime import datetime
from decimal import Decimal
import os
from typing import Any, Dict, Optional

import requests
from sqlalchemy.orm import Session, joinedload

from models import Lead, Invoice, Contact

PIXEL_ID = "your_pixel_id_here" or os.getenv("META_PIXEL_ID")
ACCESS_TOKEN = "your_access_token_here" or os.getenv("META_ACCESS_TOKEN")



def build_meta_payload_for_lead(lead_id: int, db: Session) -> Dict[str, Any]:
    """
    Build a Meta (Conversions API) payload for a Lead.
    Returns a dict ready to be JSON-serialized and POSTed to Meta's /{pixel_id}/events endpoint.
    """
    lead = (
        db.query(Lead)
        .options(joinedload(Lead.contact), joinedload(Lead.invoices))
        .filter(Lead.id == lead_id)
        .first()
    )
    if not lead:
        raise ValueError(f"Lead {lead_id} not found")

    contact: Optional[Contact] = lead.contact

    event_time = int(datetime.utcnow().timestamp())

    user_data: Dict[str, Any] = {}
    if contact:
        if getattr(contact, "email", None):
            user_data["em"] = contact.email
        if getattr(contact, "phone", None):
            user_data["ph"] = contact.phone if hasattr(contact, "phone") else None
        # include internal UUID to help de-dup / matching (not hashed here; caller may hash)
        if getattr(contact, "uuid", None):
            user_data["client_user_id"] = str(contact.uuid)

    # Aggregate invoice amounts if present
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    if lead.invoices:
        sum_amount = Decimal("0")
        for inv in lead.invoices:
            if inv.amount is not None:
                sum_amount += Decimal(inv.amount)
            if not currency and getattr(inv, "currency", None):
                currency = inv.currency
        total_amount = float(sum_amount) if sum_amount != Decimal("0") else None

    custom_data: Dict[str, Any] = {
        "lead_id": lead.id,
        "lead_status": lead.status,
    }
    if total_amount is not None:
        custom_data["value"] = total_amount
    if currency:
        custom_data["currency"] = currency

    event: Dict[str, Any] = {
        "event_name": "Lead",
        "event_time": event_time,
        "event_id": f"lead_{lead.id}",
        "action_source": "instagram",
        "user_data": user_data or None,
        "custom_data": custom_data,
    }

    return {"data": [event]}


def post_payload_to_meta(pixel_id: str, access_token: str, payload: Dict[str, Any], timeout: int = 10) -> requests.Response:
    """
    Send the built payload to Meta's Conversions API.
    Returns requests.Response for caller handling.
    """
    url = f"https://graph.facebook.com/v16.0/{pixel_id}/events"
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}
    resp = requests.post(url, params=params, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp