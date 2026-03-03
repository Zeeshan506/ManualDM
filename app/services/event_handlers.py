from sqlalchemy.orm import Session

from app.db.models import Lead
from app.services.meta_conversion_events import persist_contact_event_for_lead
from utils import upsert_lead_from_payload, track_inbound_chat_history_messages


def has_referral(data: dict) -> bool:
    if not isinstance(data, dict):
        return False

    entries = data.get("entry", [])
    for entry in entries:
        messaging = entry.get("messaging", [])
        for msg in messaging:
            if "referral" in msg:
                return True
            if msg.get("field") == "messaging_referral":
                value = msg.get("value", {})
                if "referral" in value:
                    return True

    return False


def handle_event_received(data: dict, db: Session) -> dict:
    result: dict = {
        "lead_result": None,
        "saved_inbound_count": 0,
        "saved_inbound_messages": [],
        "async_jobs": [],
        "enqueue_reasons": [],
    }

    lead_result = upsert_lead_from_payload(data, db)
    result["lead_result"] = lead_result

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

        saved_messages = track_inbound_chat_history_messages(
            data,
            igsid=lead_result["igsid"],
            db=db,
        )
        saved_payloads = [
            {
                "id": int(msg.id),
                "text": msg.text_cleaned or msg.text_raw or "📷 [Media/Attachment]",
                "direction": msg.direction,
                "time": msg.created_at.strftime("%I:%M %p") if msg.created_at else None,
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
                "type": "new_message",
            }
            for msg in saved_messages
        ]

        result["saved_inbound_messages"] = saved_payloads
        result["saved_inbound_count"] = len(saved_payloads)
        print(f"Inbound chat messages saved: {len(saved_payloads)}")
    else:
        print("No sender id found in payload - nothing to upsert or reply to")

    return result
