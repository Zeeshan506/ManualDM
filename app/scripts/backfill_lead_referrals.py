from __future__ import annotations

import argparse
from typing import Any, Dict, Optional, Set

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.db.models import Contact, Lead, WebhookEvent


def _extract_referral_from_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None

    message = payload.get("message")
    if isinstance(message, dict):
        message_referral = message.get("referral")
        if isinstance(message_referral, dict):
            return message_referral

    direct_referral = payload.get("referral")
    if isinstance(direct_referral, dict):
        return direct_referral

    value_referral = payload.get("value")
    if isinstance(value_referral, dict):
        nested_referral = value_referral.get("referral")
        if isinstance(nested_referral, dict):
            return nested_referral

    postback = payload.get("postback")
    if isinstance(postback, dict):
        postback_referral = postback.get("referral")
        if isinstance(postback_referral, dict):
            return postback_referral

    for value in payload.values():
        if isinstance(value, dict):
            found = _extract_referral_from_payload(value)
            if found:
                return found
        elif isinstance(value, list):
            for item in value:
                found = _extract_referral_from_payload(item)
                if found:
                    return found

    return None


def _extract_sender_ids(payload: Any) -> Set[str]:
    sender_ids: Set[str] = set()
    if not isinstance(payload, dict):
        return sender_ids

    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue

        for event in entry.get("messaging", []):
            if not isinstance(event, dict):
                continue
            sender_id = (event.get("sender") or {}).get("id")
            if sender_id:
                sender_ids.add(str(sender_id))

        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue

            for message in value.get("messages", []):
                if not isinstance(message, dict):
                    continue
                sender_id = message.get("from")
                if sender_id:
                    sender_ids.add(str(sender_id))

    return sender_ids


def _is_missing_referral(lead: Lead) -> bool:
    referral_payload = getattr(lead, "referral_payload", None)
    return not isinstance(referral_payload, dict) or not referral_payload


def backfill_lead_referrals(*, limit: Optional[int], dry_run: bool) -> None:
    db: Session = SessionLocal()

    scanned = 0
    missing_referral = 0
    updated = 0
    no_contact = 0
    no_webhook_referral_found = 0
    scanned_webhook_events = 0

    try:
        leads = db.query(Lead).order_by(Lead.id.asc())
        if limit is not None and limit > 0:
            leads = leads.limit(limit)

        lead_rows = leads.all()

        leads_by_igsid: Dict[str, Lead] = {}
        contacts_by_igsid: Dict[str, Contact] = {}

        for lead in lead_rows:
            scanned += 1
            if not _is_missing_referral(lead):
                continue

            missing_referral += 1

            if not lead.contact_id:
                no_contact += 1
                continue

            contact = db.query(Contact).filter(Contact.id == lead.contact_id).first()
            if not contact:
                no_contact += 1
                continue

            leads_by_igsid[str(contact.igsid)] = lead
            contacts_by_igsid[str(contact.igsid)] = contact

        unresolved_igsids = set(leads_by_igsid.keys())
        referral_by_igsid: Dict[str, Dict[str, Any]] = {}

        if unresolved_igsids:
            webhook_events = (
                db.query(WebhookEvent)
                .order_by(WebhookEvent.created_at.desc(), WebhookEvent.id.desc())
                .all()
            )

            for webhook_event in webhook_events:
                if not unresolved_igsids:
                    break

                scanned_webhook_events += 1
                payload = webhook_event.payload
                referral = _extract_referral_from_payload(payload)
                if not referral:
                    continue

                sender_ids = _extract_sender_ids(payload)
                if not sender_ids:
                    continue

                for sender_id in sender_ids:
                    if sender_id in unresolved_igsids:
                        referral_by_igsid[sender_id] = referral
                        unresolved_igsids.remove(sender_id)

        for igsid, lead in leads_by_igsid.items():
            found_referral = referral_by_igsid.get(igsid)
            if not found_referral:
                no_webhook_referral_found += 1
                continue

            lead.referral_payload = found_referral

            contact = contacts_by_igsid.get(igsid)
            if contact and not contact.referral_id:
                referral_id = (
                    found_referral.get("ref")
                    or found_referral.get("referral_id")
                    or found_referral.get("source")
                )
                if referral_id is not None:
                    contact.referral_id = str(referral_id)

            updated += 1

        if dry_run:
            db.rollback()
        else:
            db.commit()

        mode = "DRY RUN" if dry_run else "WRITE"
        print(f"[{mode}] Lead referral backfill complete")
        print(f"Scanned leads: {scanned}")
        print(f"Leads missing referral field: {missing_referral}")
        print(f"Leads updated: {updated}")
        print(f"Leads skipped (no contact): {no_contact}")
        print(f"Webhook events scanned: {scanned_webhook_events}")
        print(f"Leads skipped (no referral found in webhook payloads): {no_webhook_referral_found}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill leads.referral_payload from historical messages if missing."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only first N leads (ordered by lead id).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and report only; do not commit changes.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    backfill_lead_referrals(limit=args.limit, dry_run=args.dry_run)
