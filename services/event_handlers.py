import os
from typing import Any
from sqlalchemy.orm import Session

from utils import (
    upsert_lead_from_payload,
    track_inbound_chat_history,
    automation_mail,
    append_chat_message,
)
from services.meta_conversion_events import persist_viewcontent_event_for_lead


def has_referral(data: dict) -> bool:
    """Check if the webhook payload contains a referral field."""
    if not isinstance(data, dict):
        return False
    
    entries = data.get("entry", [])
    for entry in entries:
        messaging = entry.get("messaging", [])
        for msg in messaging:
            if "referral" in msg:
                return True
            # Check if field is messaging_referral
            if msg.get("field") == "messaging_referral":
                value = msg.get("value", {})
                if "referral" in value:
                    return True
    
    return False


def handle_event_received(data: dict, db: Session) -> dict:
    """Orchestrate lead upsert, inbound tracking and optional auto-reply.

    Returns a summary dict with keys: `lead_result`, `saved_inbound_count`, `automation_result`.
    """
    result: dict = {"lead_result": None, "saved_inbound_count": 0, "automation_result": None}
    # Ensure a contact/lead record exists for the sender so we can track and reply.
    # This fixes cases where the very first DM from a new user wasn't triggering the
    # auto-reply because lead creation was gated by referral presence.
    lead_result = None
    try:
        lead_result = upsert_lead_from_payload(data, db)
    except Exception as exc:
        print(f"⚠️ upsert_lead_from_payload failed: {exc}")

    result["lead_result"] = lead_result

    # If we have a sender, persist inbound messages and possibly send a greeting
    if lead_result:
        action = "created" if lead_result.get("created_lead") or lead_result.get("created_contact") else "updated"
        print(f"Lead {action}: id={lead_result.get('lead_id')} igsid={lead_result.get('igsid')}")

        if lead_result.get("created_lead"):
            try:
                meta_event = persist_viewcontent_event_for_lead(
                    db,
                    lead_id=int(lead_result["lead_id"]),
                    igsid=str(lead_result["igsid"]),
                )
                if meta_event:
                    print(f"MetaConversionEvent created: id={meta_event.id} event_name={meta_event.event_name}")
                else:
                    print("MetaConversionEvent skipped (already exists): event_name=ViewContent")
            except Exception as exc:
                print(f"⚠️ Failed to persist ViewContent meta event: {exc}")

        saved = track_inbound_chat_history(
            data,
            igsid=lead_result["igsid"],
            db=db,
        )
        result["saved_inbound_count"] = saved
        print(f"Inbound chat messages saved: {saved}")

        # First-time greeting: send when either the contact or lead was created
        created_first_time = bool(lead_result.get("created_contact") or lead_result.get("created_lead") or lead_result.get("created"))
        if created_first_time:
            sender_id = lead_result.get("igsid")
            try:
                automation_result = automation_mail(sender_id)
                result["automation_result"] = automation_result
                print(f"Automation mail result for IGSID {sender_id}: {automation_result}")

                if automation_result is not None:
                    append_chat_message(
                        db,
                        igsid=str(sender_id),
                        direction="outbound",
                        message_text=os.getenv("IG_AUTOREPLY_TEXT"),
                        platform_message_id=(automation_result.get("message_id") if isinstance(automation_result, dict) else None),
                        payload=automation_result if isinstance(automation_result, dict) else None,
                    )
            except Exception as exc:
                print(f"⚠️ Failed to send automated message: {exc}")
    else:
        # No sender could be determined; try to record history if possible.
        print("No sender id found in payload - nothing to upsert or reply to")

    return result
