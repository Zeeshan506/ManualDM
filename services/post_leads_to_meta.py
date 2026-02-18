import os
from typing import Any, Dict, List, Optional, Sequence

import requests
from sqlalchemy.orm import Session

from models import MetaConversionEvent

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v16.0")
PIXEL_ID = os.getenv("META_PIXEL_ID")
ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")


def _resolve_meta_credentials(
    pixel_id: Optional[str] = None,
    access_token: Optional[str] = None,
) -> tuple[str, str]:
    resolved_pixel_id = pixel_id or PIXEL_ID
    resolved_access_token = access_token or ACCESS_TOKEN

    if not resolved_pixel_id:
        raise ValueError("Missing META_PIXEL_ID")
    if not resolved_access_token:
        raise ValueError("Missing META_ACCESS_TOKEN")

    return resolved_pixel_id, resolved_access_token


def build_meta_payload_for_conversion_event(event: MetaConversionEvent) -> Dict[str, Any]:
    """
    Build a Meta Conversions API payload directly from a MetaConversionEvent row.
    """
    event_payload: Dict[str, Any] = {
        "event_name": event.event_name,
        "event_time": int(event.event_time),
        "action_source": event.action_source or "business_messaging",
        "messaging_channel": event.messaging_channel or "instagram",
    }

    if event.user_data:
        event_payload["user_data"] = event.user_data
    if event.custom_data:
        event_payload["custom_data"] = event.custom_data

    payload: Dict[str, Any] = {"data": [event_payload]}
    if event.partner_agent:
        payload["partner_agent"] = event.partner_agent
    return payload


def build_meta_payload_for_event_id(event_id: int, db: Session) -> Dict[str, Any]:
    """
    Load a MetaConversionEvent by id and build a request payload for Meta CAPI.
    """
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
    """
    Query conversion events from DB to prepare posting to Meta.
    """
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
    """
    Send payload to Meta Conversions API.
    """
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{pixel_id}/events"
    params = {"access_token": access_token}
    headers = {"Content-Type": "application/json"}
    response = requests.post(url, params=params, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response


def post_meta_event_by_id(
    db: Session,
    *,
    event_id: int,
    pixel_id: Optional[str] = None,
    access_token: Optional[str] = None,
    timeout: int = 10,
) -> Dict[str, Any]:
    """
    Post one MetaConversionEvent row to Meta CAPI by event id.
    """
    resolved_pixel_id, resolved_access_token = _resolve_meta_credentials(
        pixel_id=pixel_id,
        access_token=access_token,
    )

    payload = build_meta_payload_for_event_id(event_id, db)
    response = post_payload_to_meta(
        resolved_pixel_id,
        resolved_access_token,
        payload,
        timeout=timeout,
    )

    response_body: Any
    try:
        response_body = response.json()
    except ValueError:
        response_body = response.text

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
    """
    Post multiple conversion events loaded from DB rows.

    Note:
      - This function does not mutate event rows (no sent/failed marker columns exist yet).
    """
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
        response = post_payload_to_meta(
            resolved_pixel_id,
            resolved_access_token,
            payload,
            timeout=timeout,
        )
        try:
            response_body: Any = response.json()
        except ValueError:
            response_body = response.text

        results.append(
            {
                "event_id": event.id,
                "event_name": event.event_name,
                "status_code": response.status_code,
                "response": response_body,
            }
        )

    return results