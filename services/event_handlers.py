import os
from typing import Any
from sqlalchemy.orm import Session

from utils import (
    upsert_lead_from_payload,
    track_inbound_chat_history,
    automation_mail,
    append_chat_message,
)


def handle_event_received(data: dict, db: Session) -> dict:
    """Orchestrate lead upsert, inbound tracking and optional auto-reply.

    Returns a summary dict with keys: `lead_result`, `saved_inbound_count`, `automation_result`.
    """
    result: dict = {"lead_result": None, "saved_inbound_count": 0, "automation_result": None}

    lead_result = upsert_lead_from_payload(data, db)
    result["lead_result"] = lead_result

    if lead_result:
        action = "created" if lead_result.get("created") else "updated"
        print(
            f"Lead {action}: id={lead_result.get('lead_id')} instagram_user_id={lead_result.get('instagram_user_id')}"
        )

        saved = track_inbound_chat_history(
            data,
            lead_id=lead_result["lead_id"],
            instagram_user_id=lead_result["instagram_user_id"],
            db=db,
        )
        result["saved_inbound_count"] = saved
        print(f"Inbound chat messages saved: {saved}")

        # First-time greeting only
        if lead_result and lead_result.get("created"):
            sender_id = lead_result.get("instagram_user_id")
            try:
                automation_result = automation_mail(sender_id)
                result["automation_result"] = automation_result
                print(f"Automation mail result for PSID {sender_id}: {automation_result}")

                if automation_result is not None:
                    append_chat_message(
                        db,
                        lead_id=lead_result["lead_id"],
                        instagram_user_id=str(sender_id),
                        direction="outbound",
                        message_text=os.getenv("IG_AUTOREPLY_TEXT"),
                        platform_message_id=(automation_result.get("message_id") if isinstance(automation_result, dict) else None),
                        payload=automation_result if isinstance(automation_result, dict) else None,
                    )
            except Exception as exc:
                print(f"⚠️ Failed to send automated message: {exc}")

    return result
