import os
from typing import Any
from sqlalchemy.orm import Session

from utils import (
    upsert_lead_from_payload,
    track_inbound_chat_history,
    automation_mail,
    append_chat_message,
)


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

    # Check if this event contains a referral
    has_referral_data = has_referral(data)

    # Only create/update lead if referral is present
    if has_referral_data:
        lead_result = upsert_lead_from_payload(data, db)
        result["lead_result"] = lead_result

        if lead_result:
            action = "created" if lead_result.get("created") else "updated"
            print(
                f"Lead {action}: id={lead_result.get('lead_id')} igsid={lead_result.get('igsid')}"
            )

            saved = track_inbound_chat_history(
                data,
                igsid=lead_result["igsid"],
                db=db,
            )
            result["saved_inbound_count"] = saved
            print(f"Inbound chat messages saved: {saved}")

            # First-time greeting only
            if lead_result and lead_result.get("created"):
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
        # No referral: only track messaging history without creating a lead
        print("No referral found - tracking messaging history only")
        # Extract igsid from the payload for tracking
        igsid = None
        entries = data.get("entry", [])
        for entry in entries:
            messaging = entry.get("messaging", [])
            for msg in messaging:
                sender = msg.get("sender", {})
                igsid = sender.get("id")
                break
            if igsid:
                break
        
        if igsid:
            saved = track_inbound_chat_history(
                data,
                igsid=igsid,
                db=db,
            )
            result["saved_inbound_count"] = saved
            print(f"Inbound chat messages saved (no lead): {saved}")

    return result
