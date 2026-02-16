import hashlib
import json
import requests
from dotenv import load_dotenv
import os
load_dotenv()

VOLATILE_KEYS = {"timestamp", "time", "sent_time", "created_time", "sent_at"}

ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN_ZESHAN6A")


def automation_mail(psid, message_text=None):
    """
    Docstring for automation_mail
    
    :param psid: Instagram recipient id
    :param message_text: Optional message text override

    will send a message to the user with the given psid using the Instagram Graph API.
    """

    access_token = os.getenv("IG_ACCESS_TOKEN_ZESHAN6A")
    ig_account_id = os.getenv("IG_ACCOUNT_ID")
    graph_version = os.getenv("IG_GRAPH_VERSION")
    messaging_product = os.getenv("IG_MESSAGING_PRODUCT")
    default_auto_reply = os.getenv("IG_AUTOREPLY_TEXT")
    print(access_token, ig_account_id, graph_version, messaging_product, default_auto_reply, sep="\n")

    if not access_token or not ig_account_id or not graph_version or not messaging_product:
        print("Missing one or more required env vars: IG_ACCESS_TOKEN (or IG_ACCESS_TOKEN_ZESHAN6A), IG_ACCOUNT_ID, IG_GRAPH_VERSION, IG_MESSAGING_PRODUCT")
        return None

    final_text = message_text or default_auto_reply
    if not final_text:
        print("Missing auto-reply text. Set IG_AUTOREPLY_TEXT or pass message_text.")
        return None

    api_url = f"https://graph.instagram.com/v{graph_version}/{ig_account_id}/messages"
    headers = {"Content-Type": "application/json"}
    params = {"access_token": access_token}
    data = {
        "messaging_product": messaging_product,
        "recipient": {"id": str(psid)},
        "message": {"text": final_text},
    }
    print(f"Sending automation message to PSID {psid}")
    try:
        response = requests.post(api_url, headers=headers, params=params, json=data, timeout=15)
        response.raise_for_status()
        print(f"Message sent successfully to PSID {psid}")
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Failed to send message to PSID {psid}: {e}")
        return None

def fetch_user_info():
    """
    will fetch user info from the database and return it as a list of dictionaries.
    will include the psid of the sender, users info will be taken from env.
    """

    pass


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


def compute_fingerprint(payload):
    if not isinstance(payload, (dict, list)):
        return None
    try:
        stable = _strip_volatile(payload)
        serialized = json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha255(serialized.encode("utf-8")).hexdigest()
    except Exception:
        return None
        
def _extract_inbound_sender_id(payload):
    if not isinstance(payload, dict):
        return None

    entries = payload.get("entry") or []
    for entry in entries:
        messaging_events = entry.get("messaging") or []
        for event in messaging_events:
            sender_id = (event.get("sender") or {}).get("id")
            message = event.get("message") or {}
            if sender_id and message and not message.get("is_echo"):
                return sender_id

        changes = entry.get("changes") or []
        for change in changes:
            value = change.get("value") or {}
            messages = value.get("messages") or []
            for message in messages:
                sender_id = message.get("from")
                if sender_id:
                    return sender_id

    return None