import copy
import json
import os
from typing import Any, Dict, List, Optional, Sequence

import requests
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.db.models import MetaConversionEvent

load_dotenv()

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION") or os.getenv("IG_GRAPH_VERSION") or "v25.0"
PIXEL_ID = os.getenv("DATASET_ID") or os.getenv("META_PIXEL_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")


def _is_viewcontent_payload(payload: Dict[str, Any]) -> bool:
    data = payload.get("data")
    if not isinstance(data, list):
        return False

    for item in data:
        if not isinstance(item, dict):
            continue
        if str(item.get("event_name") or "") == "ViewContent":
            return True

    return False


def _normalize_business_messaging_event(event_payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(event_payload)
    if (
        str(normalized.get("action_source") or "").lower() == "business_messaging"
        and str(normalized.get("event_name") or "") == "Contact"
    ):
        normalized["event_name"] = "ViewContent"
        custom_data = normalized.get("custom_data") if isinstance(normalized.get("custom_data"), dict) else {}
        custom_data.setdefault("original_event_name", "Contact")
        custom_data.setdefault("trigger", "referral")
        normalized["custom_data"] = custom_data
    return normalized


def _extract_response_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _resolve_meta_credentials(
    pixel_id: Optional[str] = None,
    access_token: Optional[str] = None,
) -> tuple[str, str]:
    resolved_pixel_id = pixel_id or PIXEL_ID
    resolved_access_token = access_token or ACCESS_TOKEN

    if not resolved_pixel_id:
        raise ValueError("Missing DATASET_ID (or META_PIXEL_ID)")
    if not resolved_access_token:
        raise ValueError("Missing ACCESS_TOKEN (or META_ACCESS_TOKEN)")

    return resolved_pixel_id, resolved_access_token


def build_meta_payload_for_conversion_event(event: MetaConversionEvent) -> Dict[str, Any]:
    if isinstance(event.full_payload, dict):
        data = event.full_payload.get("data")
        if isinstance(data, list) and data:
            payload: Dict[str, Any] = {
                "data": [_normalize_business_messaging_event(item) for item in data if isinstance(item, dict)]
            }
            if event.partner_agent:
                payload["partner_agent"] = event.partner_agent
            return payload

    event_payload: Dict[str, Any] = {
        "event_name": event.event_name,
        "event_time": int(event.event_time),
        "action_source": event.action_source or "business_messaging",
        "messaging_channel": event.messaging_channel or "instagram",
    }

    if event.event_id:
        event_payload["event_id"] = event.event_id

    if event.user_data:
        event_payload["user_data"] = event.user_data
    if event.custom_data:
        event_payload["custom_data"] = event.custom_data

    payload: Dict[str, Any] = {"data": [_normalize_business_messaging_event(event_payload)]}
    if event.partner_agent:
        payload["partner_agent"] = event.partner_agent
    return payload


def build_meta_payload_for_event_id(event_id: int, db: Session) -> Dict[str, Any]:
    event = db.query(MetaConversionEvent).filter(MetaConversionEvent.id == event_id).first()
    if not event:
        raise ValueError(f"MetaConversionEvent {event_id} not found")

    return build_meta_payload_for_conversion_event(event)


def get_meta_conversion_events(
    db: Session,
    *,
    event_ids: Optional[Sequence[int]] = None,
    lead_id: Optional[int] = None,
    event_name: Optional[str] = None,
    limit: int = 50,
) -> List[MetaConversionEvent]:
    query = db.query(MetaConversionEvent)

    if event_ids:
        query = query.filter(MetaConversionEvent.id.in_(list(event_ids)))
    if lead_id is not None:
        query = query.filter(MetaConversionEvent.lead_id == lead_id)
    if event_name:
        query = query.filter(MetaConversionEvent.event_name == event_name)

    return (
        query.order_by(MetaConversionEvent.created_at.asc(), MetaConversionEvent.id.asc())
        .limit(limit)
        .all()
    )


def post_payload_to_meta(
    pixel_id: str,
    access_token: str,
    payload: Dict[str, Any],
    timeout: int = 10,
) -> requests.Response:
    normalized_graph_version = GRAPH_VERSION if str(GRAPH_VERSION).startswith("v") else f"v{GRAPH_VERSION}"
    url = f"https://graph.facebook.com/{normalized_graph_version}/{pixel_id}/events"
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, params=params, json=payload, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        response_body = _extract_response_body(response)
        raise RuntimeError(
            "Meta CAPI request failed "
            f"status={response.status_code} "
            f"url={response.url} "
            f"response={json.dumps(response_body, ensure_ascii=False)} "
            f"payload={json.dumps(payload, ensure_ascii=False)}"
        )
    return response


def post_meta_event_by_id(
    db: Session,
    *,
    event_id: int,
    pixel_id: Optional[str] = None,
    access_token: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    event = db.query(MetaConversionEvent).filter(MetaConversionEvent.id == event_id).first()
    if not event:
        raise ValueError(f"MetaConversionEvent {event_id} not found")

    if str(event.event_name or "") == "ViewContent":
        return {
            "event_id": event_id,
            "status": "skipped",
            "reason": "viewcontent_not_sent",
        }

    resolved_pixel_id, resolved_access_token = _resolve_meta_credentials(
        pixel_id=pixel_id,
        access_token=access_token,
    )

    payload = build_meta_payload_for_conversion_event(event)
    if _is_viewcontent_payload(payload):
        return {
            "event_id": event_id,
            "status": "skipped",
            "reason": "viewcontent_not_sent",
        }

    response = post_payload_to_meta(
        resolved_pixel_id,
        resolved_access_token,
        payload,
        timeout=timeout,
    )

    response_body: Any = _extract_response_body(response)

    return {
        "event_id": event_id,
        "status_code": response.status_code,
        "response": response_body,
    }


def post_meta_events_batch(
    db: Session,
    *,
    event_ids: Optional[Sequence[int]] = None,
    lead_id: Optional[int] = None,
    event_name: Optional[str] = None,
    limit: int = 50,
    pixel_id: Optional[str] = None,
    access_token: Optional[str] = None,
    timeout: int = 10,
) -> List[Dict[str, Any]]:
    resolved_pixel_id, resolved_access_token = _resolve_meta_credentials(
        pixel_id=pixel_id,
        access_token=access_token,
    )

    events = get_meta_conversion_events(
        db,
        event_ids=event_ids,
        lead_id=lead_id,
        event_name=event_name,
        limit=limit,
    )

    results: List[Dict[str, Any]] = []
    for event in events:
        payload = build_meta_payload_for_conversion_event(event)
        if str(event.event_name or "") == "ViewContent" or _is_viewcontent_payload(payload):
            results.append(
                {
                    "event_id": event.id,
                    "event_name": event.event_name,
                    "status": "skipped",
                    "reason": "viewcontent_not_sent",
                }
            )
            continue

        response = post_payload_to_meta(
            resolved_pixel_id,
            resolved_access_token,
            payload,
            timeout=timeout,
        )
        response_body: Any = _extract_response_body(response)

        results.append(
            {
                "event_id": event.id,
                "event_name": event.event_name,
                "status_code": response.status_code,
                "response": response_body,
            }
        )

    return results
