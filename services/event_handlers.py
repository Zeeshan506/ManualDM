from sqlalchemy.orm import Session

from models import Lead
from utils import (
    upsert_lead_from_payload,
    track_inbound_chat_history,
)
from services.meta_conversion_events import persist_contact_event_for_lead


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

    Returns a summary dict with keys: `lead_result`, `saved_inbound_count`, `async_jobs`.
    """
    result: dict = {
        "lead_result": None,
        "saved_inbound_count": 0,
        "async_jobs": [],
        "enqueue_reasons": [],
    }
    # Ensure a contact/lead record exists for the sender so we can track and reply.
    # This fixes cases where the very first DM from a new user wasn't triggering the
    # auto-reply because lead creation was gated by referral presence.
    lead_result = upsert_lead_from_payload(data, db)

    result["lead_result"] = lead_result

    # If we have a sender, persist inbound messages and possibly send a greeting
    if lead_result:
        action = "created" if lead_result.get("created_lead") or lead_result.get("created_contact") else "updated"
        print(f"Lead {action}: id={lead_result.get('lead_id')} igsid={lead_result.get('igsid')}")

        if lead_result.get("created_lead") and lead_result.get("lead_id"):
            created_lead = db.query(Lead).filter(Lead.id == int(lead_result["lead_id"])).first()
            if created_lead:
                created_lead.assigned_to = None
                created_lead.lead_status = "unassigned"
                db.flush()

        if has_referral(data):
            meta_event = persist_contact_event_for_lead(
                db,
                lead_id=int(lead_result["lead_id"]),
                igsid=str(lead_result["igsid"]),
            )
            print(f"MetaConversionEvent created: id={meta_event.id} event_name={meta_event.event_name}")
            result["async_jobs"].append(
                {
                    "type": "post_meta_conversion_event",
                    "event_id": int(meta_event.id),
                }
            )
            result["enqueue_reasons"].append("referral_contact")

        saved = track_inbound_chat_history(
            data,
            igsid=lead_result["igsid"],
            db=db,
        )
        result["saved_inbound_count"] = saved
        print(f"Inbound chat messages saved: {saved}")

        # First-time greeting automation is intentionally disabled for now.
        # Keep this block for future re-enable.
        # created_first_time = bool(
        #     lead_result.get("created_contact")
        #     or lead_result.get("created_lead")
        #     or lead_result.get("created")
        # )
        # if created_first_time:
        #     sender_id = lead_result.get("igsid")
        #     if sender_id:
        #         result["async_jobs"].append(
        #             {
        #                 "type": "send_automation_reply",
        #                 "igsid": str(sender_id),
        #             }
        #         )
        #         result["enqueue_reasons"].append("first_time_contact")
        # else:
        #     print("No automation enqueue: sender already exists (not first-time)")
    else:
        # No sender could be determined; try to record history if possible.
        print("No sender id found in payload - nothing to upsert or reply to")

    return result
