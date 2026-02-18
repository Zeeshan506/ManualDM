import hashlib
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from models import Message, Contact, Lead

load_dotenv()

# -----------------------------
# Constants & Volatile filters
# -----------------------------
VOLATILE_KEYS = {"timestamp", "time", "sent_time", "created_time", "sent_at"}
ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN_ZESHAN6A")


# -----------------------------
# Environment helpers
# -----------------------------
def _get_env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return environment variable value or default."""
    return os.getenv(key, default)


# -----------------------------
# Messaging / Network
# -----------------------------
def automation_mail(psid: str, message_text: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Send an automated Instagram message to the given PSID using Graph API.

    Args:
        psid: Instagram recipient id (IGSID).
        message_text: Optional override for auto-reply text. If omitted, IG_AUTOREPLY_TEXT is used.

    Returns:
        Parsed JSON response on success, or None on failure / missing configuration.
    """
    access_token = _get_env("IG_ACCESS_TOKEN_ZESHAN6A") or _get_env("IG_ACCESS_TOKEN")
    ig_account_id = _get_env("IG_ACCOUNT_ID")
    graph_version = _get_env("IG_GRAPH_VERSION")
    messaging_product = _get_env("IG_MESSAGING_PRODUCT")
    default_auto_reply = _get_env("IG_AUTOREPLY_TEXT")

    if not (access_token and ig_account_id and graph_version and messaging_product):
        print("Missing required IG env vars (ACCESS_TOKEN, ACCOUNT_ID, GRAPH_VERSION, MESSAGING_PRODUCT).")
        return None

    final_text = message_text or default_auto_reply
    if not final_text:
        print("Missing auto-reply text. Set IG_AUTOREPLY_TEXT or pass message_text.")
        return None

    api_url = f"https://graph.instagram.com/v{graph_version}/{ig_account_id}/messages"
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}
    payload = {
        "messaging_product": messaging_product,
        "recipient": {"id": str(psid)},
        "message": {"text": final_text},
    }

    try:
        resp = requests.post(api_url, headers=headers, params=params, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as exc:
        print(f"Failed to send message to IGSID {psid}: {exc}")
        return None


# -----------------------------
# Fetch / Lookup helpers
# -----------------------------
def fetch_user_info(db: Optional[Session] = None) -> List[Dict[str, Any]]:
    """
    Fetch user info list.

    Behavior:
      - If a SQLAlchemy Session is provided, query the Contact table and return basic records.
      - Otherwise, attempt to read IG_MONITORED_USERS env var. Supported formats:
          * JSON array of objects -> returned as-is
          * Comma-separated list of IGSIDs -> returned as [{"igsid": id}, ...]

    Returns:
        List of dictionaries describing users.
    """
    if db:
        try:
            rows = db.query(Contact).all()
            result = []
            for c in rows:
                lead = c.lead
                result.append(
                    {
                        "contact_id": c.id,
                        "lead_id": lead.id if lead else None,
                        "igsid": c.igsid,
                        "status": lead.status if lead else None,
                        "flow_step": None,
                        "last_message_at": c.last_message_at,
                    }
                )
            return result
        except Exception:
            return []

    raw = _get_env("IG_MONITORED_USERS", "")
    raw = raw.strip()
    if not raw:
        return []

    # Try JSON
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    # Fallback: comma separated IGSIDs
    return [{"igsid": p.strip()} for p in raw.split(",") if p.strip()]


# -----------------------------
# Payload extractors
# -----------------------------
def _extract_inbound_message_text(payload: Any) -> Optional[str]:
    """
    Extract the first inbound message text from a webhook payload.

    Supports Messenger-style (entry.messaging[].message.text) and
    Instagram 'changes' style (entry.changes[].value.messages[].text.body).
    """
    if not isinstance(payload, dict):
        return None

    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            text = (event.get("message") or {}).get("text")
            if isinstance(text, str):
                return text

        for change in entry.get("changes", []):
            for msg in (change.get("value") or {}).get("messages", []):
                text = (msg.get("text") or {}).get("body")
                if isinstance(text, str):
                    return text
    return None


def _extract_inbound_sender_id(payload: Any) -> Optional[str]:
    """
    Extract the sender id (IGSID) from a webhook payload.

    Returns the first non-echo sender id found.
    """
    if not isinstance(payload, dict):
        return None

    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = (event.get("sender") or {}).get("id")
            message = event.get("message") or {}
            if sender_id and message and not message.get("is_echo"):
                return sender_id

        for change in entry.get("changes", []):
            for msg in (change.get("value") or {}).get("messages", []):
                sender_id = msg.get("from")
                if sender_id:
                    return sender_id
    return None


def _extract_inbound_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Normalize inbound messages from payload into a list of dicts:
      { igsid, message_text, platform_message_id, payload }
    """
    if not isinstance(payload, dict):
        return []

    extracted: List[Dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for event in entry.get("messaging", []):
            sender_id = (event.get("sender") or {}).get("id")
            message = event.get("message") or {}
            text = message.get("text")
            mid = message.get("mid")
            if sender_id and (text is not None or mid is not None):
                extracted.append(
                    {
                        "igsid": str(sender_id),
                        "message_text": text,
                        "platform_message_id": mid,
                        "payload": event,
                    }
                )

        for change in entry.get("changes", []):
            value = change.get("value") or {}
            for msg in value.get("messages", []):
                sender_id = msg.get("from")
                text = (msg.get("text") or {}).get("body")
                mid = msg.get("id")
                if sender_id and (text is not None or mid is not None):
                    extracted.append(
                        {
                            "igsid": str(sender_id),
                            "message_text": text,
                            "platform_message_id": mid,
                            "payload": msg,
                        }
                    )
    return extracted


# -----------------------------
# Fingerprinting / Volatile stripping
# -----------------------------
def _strip_volatile(obj: Any) -> Any:
    """Recursively remove volatile keys from dicts/lists used for stable fingerprinting."""
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def compute_fingerprint(payload: Any) -> Optional[str]:
    """
    Compute a deterministic SHA-256 fingerprint for a payload after removing volatile fields.

    Returns:
        Hex digest string or None if payload is not serializable.
    """
    if not isinstance(payload, (dict, list)):
        return None
    try:
        stable = _strip_volatile(payload)
        serialized = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    except Exception:
        return None


# -----------------------------
# Database upserts / chat history
# -----------------------------
def upsert_lead_from_payload(payload: Dict[str, Any], db: Session) -> Optional[Dict[str, Any]]:
    """
    Create or update a Lead (and Contact) based on incoming payload sender id.

    Returns a dict with lead_id, igsid, created (bool) and last_message_text.
    """
    sender_id = _extract_inbound_sender_id(payload)
    if not sender_id:
        return None

    message_text = _extract_inbound_message_text(payload)
    now = datetime.utcnow()

    try:
        contact = db.query(Contact).filter(Contact.igsid == str(sender_id)).first()
        if contact:
            contact.last_message_at = now
            created_contact = False
        else:
            contact = Contact(
                igsid=str(sender_id),
                referral_id=None,
                platform="instagram",
                created_at=now,
                last_message_at=now,
            )
            db.add(contact)
            db.flush()  # ensure id assigned for FK
            created_contact = True

        # Ensure a Lead exists for this contact (lead created when qualified)
        if contact.lead:
            lead = contact.lead
            created_lead = False
        else:
            lead = Lead(
                contact_id=contact.id,
                status="new",
                created_at=now,
            )
            db.add(lead)
            created_lead = True

        db.commit()
        # refresh to get ids
        db.refresh(contact)
        db.refresh(lead)
        return {
            "lead_id": lead.id,
            "igsid": contact.igsid,
            "created_lead": created_lead,
            "created_contact": created_contact,
            # keep legacy `created` key for backward compatibility
            "created": created_lead,
            "last_message_text": message_text,
        }
    except Exception:
        db.rollback()
        raise


def append_chat_message(
    db: Session,
    *,
    igsid: str,
    direction: str,
    message_text: Optional[str] = None,
    platform_message_id: Optional[str] = None,
    payload: Optional[Any] = None,
) -> Message:
    """
    Append a single Message row and return it.

    Args:
        db: Database session
        igsid: Instagram Sender ID (links to Contact if exists)
        direction: 'inbound' or 'outbound'
        message_text: Optional message text
        platform_message_id: Optional platform message ID
        payload: Optional raw payload data
    """
    now = datetime.utcnow()
    try:
        contact = db.query(Contact).filter(Contact.igsid == str(igsid)).first()
        if not contact:
            contact = Contact(
                igsid=str(igsid),
                platform="instagram",
                created_at=now,
                last_message_at=now,
            )
            db.add(contact)
            db.flush()

        msg = Message(
            contact_id=contact.id,
            direction=direction,
            text_raw=message_text,
            platform_message_id=platform_message_id,
            payload=payload,
            created_at=now,
        )
        db.add(msg)
        contact.last_message_at = now
        db.commit()
        db.refresh(msg)
        return msg
    except Exception:
        db.rollback()
        raise


def track_inbound_chat_history(payload: Dict[str, Any], igsid: str, db: Session) -> int:
    """
    Persist inbound messages from payload for the given IGSID.

    Args:
        payload: Webhook payload containing messages
        igsid: Instagram Sender ID
        db: Database session

    Returns:
        Number of created Message rows.
    """
    inbound = _extract_inbound_messages(payload)
    if not inbound:
        return 0

    created = 0
    now = datetime.utcnow()
    try:
        contact = db.query(Contact).filter(Contact.igsid == str(igsid)).first()
        if not contact:
            contact = Contact(
                igsid=str(igsid),
                platform="instagram",
                created_at=now,
                last_message_at=now,
            )
            db.add(contact)
            db.flush()

        for msg in inbound:
            if str(msg["igsid"]) != str(igsid):
                continue
            m = Message(
                contact_id=contact.id,
                direction="inbound",
                text_raw=msg.get("message_text"),
                platform_message_id=msg.get("platform_message_id"),
                payload=msg.get("payload"),
                created_at=now,
            )
            db.add(m)
            created += 1

        if created:
            contact.last_message_at = now

        db.commit()
        return created
    except Exception:
        db.rollback()
        raise