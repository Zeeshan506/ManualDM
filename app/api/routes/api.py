from fastapi import BackgroundTasks, Depends, HTTPException, Query, status, APIRouter
import asyncio
import logging
from pydantic import BaseModel
from sqlalchemy.orm import joinedload, Session
from app.core.database import get_db
from app.core.logging import get_logger, log_event
from sqlalchemy import func
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

from app.core.celery_app import celery_app
from app.core.dependencies import get_current_user
from app.db.models import Lead, Message, Contact, Invoice, MetaConversionEvent, User, NotificationEvent
from app.db.models import PaymentEvent
from app.services.meta_conversion_events import persist_purchase_event_for_lead
from app.services.activity_logs import enqueue_activity_log
from fastapi import WebSocket, WebSocketDisconnect
from app.core.websockets import manager
from utils import append_chat_message, automation_mail

router = APIRouter(prefix="/api", tags=["API Endpoints"])
logger = get_logger(__name__)






class CustomLeadPaymentPayload(BaseModel):
    amount: float
    currency: str = "USD"
    send_now: bool = True


class CustomMessagePayload(BaseModel):
    message_text: str


def _ensure_lead_payment_access(*, lead: Lead, current_user: User) -> None:
    if current_user.role not in {"sales_rep", "admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access lead payments",
        )


def _lead_engagement_payload(lead: Lead) -> dict:
    owner_username = None
    if lead.assigned_to is not None:
        owner_username = (
            lead.assignee.username
            if lead.assignee is not None
            else f"User #{lead.assigned_to}"
        )

    is_occupied = lead.lead_status == "active"
    occupancy_status = "occupied" if is_occupied else "unoccupied"

    return {
        "ownerUserId": lead.assigned_to,
        "ownerUsername": owner_username,
        "occupancyStatus": occupancy_status,
        "leadStatus": lead.lead_status,
        "engagedByUserId": lead.assigned_to,
        "engagedByUsername": owner_username,
        "isEngaged": is_occupied,
    }


def _lead_dead_payload(lead: Lead) -> dict:
    return {
        "deadRequested": bool(lead.dead_requested),
        "deadRequestedByUserId": lead.dead_requested_by_user_id,
        "deadRequestedAt": lead.dead_requested_at.isoformat() if lead.dead_requested_at else None,
        "deadMarkedByUserId": lead.dead_marked_by_user_id,
        "deadMarkedAt": lead.dead_marked_at.isoformat() if lead.dead_marked_at else None,
    }

@router.get("/leads")
def get_all_leads(
    status_filter: str | None = Query(None, alias="status", description="Filter leads by status (e.g., new, invoiced, paid, cancelled)"),
    assigned_to: int | None = Query(None, description="Filter active chats assigned to a specific user id"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Fetch all leads for the CRM directory.
    Joins with the Contact table to get the Instagram ID and last message timestamp.
    """
    if current_user.role not in {"sales_rep", "admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view leads",
        )

    # Start building the query, joining Lead with Contact/User to avoid N+1 query issues
    query = db.query(Lead).options(joinedload(Lead.contact), joinedload(Lead.assignee))

    normalized_status = status_filter.lower() if status_filter else None
    is_unassigned_filter = normalized_status == "unassigned"

    if assigned_to is not None:
        query = query.filter(Lead.assigned_to == assigned_to)

    # Apply optional status filter
    if normalized_status and normalized_status != "all":
        if normalized_status == "unassigned":
            query = query.filter(Lead.assigned_to.is_(None))
        else:
            query = query.filter(Lead.status == normalized_status)

    leads = query.all()

    response_data = []
    for lead in leads:
        contact = lead.contact
        
        # Determine the last active time. Fallback to lead creation if no messages exist yet.
        last_active = None
        if contact and contact.last_message_at:
            last_active = contact.last_message_at
        else:
            last_active = lead.created_at

        response_data.append({
            "id": lead.id,
            "igsid": contact.igsid if contact else None,
            "name": lead.name or "",
            "status": lead.status,
            "email": lead.email or "",
            "phone": lead.phone or "",
            "lastActive": last_active.isoformat() if last_active else None,
            **_lead_engagement_payload(lead),
            **_lead_dead_payload(lead),
        })

    # Sort the results so the most recently active leads are at the top
    response_data.sort(
        key=lambda x: x["lastActive"] if x["lastActive"] else "", 
        reverse=True
    )

    return response_data


@router.put("/leads/{lead_id}/assign")
def assign_lead_to_current_user(
    lead_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a chat as occupied and set owner only on first claim."""
    if current_user.role not in {"sales_rep", "admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to engage chats",
        )

    lead = db.query(Lead).options(joinedload(Lead.assignee)).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    if lead.assigned_to is None and current_user.id and current_user.id > 0:
        lead.assigned_to = current_user.id

    if lead.assigned_to is not None:
        lead.lead_status = "active"

    db.commit()
    db.refresh(lead)

    enqueue_activity_log(
        background_tasks,
        actor=current_user.username,
        action="CLAIM_CHAT",
        details=f"Lead #{lead.id} claimed",
        lead_id=lead.id,
        metadata={"assigned_to": lead.assigned_to, "lead_status": lead.lead_status},
    )

    return {
        "id": lead.id,
        "assigned_to": lead.assigned_to,
        "lead_status": lead.lead_status,
        **_lead_engagement_payload(lead),
    }


@router.put("/leads/{lead_id}/release")
def release_lead_engagement(
    lead_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a chat as unoccupied while preserving original owner."""
    if current_user.role not in {"sales_rep", "admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to release chats",
        )

    lead = db.query(Lead).options(joinedload(Lead.assignee)).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    if current_user.role == "sales_rep" and lead.assigned_to not in {None, current_user.id}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only release chats engaged by your account",
        )

    if lead.lead_status != "unassigned":
        lead.lead_status = "unassigned"
        db.commit()
        db.refresh(lead)

        enqueue_activity_log(
            background_tasks,
            actor=current_user.username,
            action="CLOSE_CHAT",
            details=f"Lead #{lead.id} released",
            lead_id=lead.id,
            metadata={"assigned_to": lead.assigned_to, "lead_status": lead.lead_status},
        )

    return {
        "id": lead.id,
        "assigned_to": lead.assigned_to,
        "lead_status": lead.lead_status,
        **_lead_engagement_payload(lead),
    }


@router.get("/leads/{lead_id}")
def get_lead_details(lead_id: int, db: Session = Depends(get_db)):
    """
    Fetch specific lead details for the right-hand panel in the Chat View.
    Also checks if the LeadSubmitted CAPI event has been fired.
    """
    lead = db.query(Lead).options(joinedload(Lead.contact), joinedload(Lead.assignee)).filter(Lead.id == lead_id).first()
    
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found"
        )

    # Check if a LeadSubmitted event exists for this lead in MetaConversionEvent
    capi_synced = db.query(MetaConversionEvent).filter(
        MetaConversionEvent.lead_id == lead.id,
        MetaConversionEvent.event_name == "LeadSubmitted"
    ).first() is not None

    contact = lead.contact
    
    return {
        "id": lead.id,
        "igsid": contact.igsid if contact else None,
        "name": lead.name or "",
        "status": lead.status,
        "email": lead.email or "",
        "phone": lead.phone or "",
        "metaEventFired": capi_synced,
        "createdAt": lead.created_at.isoformat(),
        **_lead_engagement_payload(lead),
        **_lead_dead_payload(lead),
    }


@router.post("/leads/{lead_id}/dead-request")
def request_lead_dead(
    lead_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "sales_rep":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only sales reps can request marking a lead as dead",
        )

    lead = db.query(Lead).options(joinedload(Lead.contact), joinedload(Lead.assignee)).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    if lead.status == "dead":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead is already marked as dead",
        )

    now_utc = datetime.utcnow()
    lead.dead_requested = True
    lead.dead_requested_by_user_id = current_user.id
    lead.dead_requested_at = now_utc
    db.commit()
    db.refresh(lead)

    notification = NotificationEvent(
        event_type="lead_dead_request",
        title="Lead dead request",
        body=f"{current_user.username} requested lead #{lead.id} to be marked dead.",
        lead_id=lead.id,
        payload={
            "lead_id": lead.id,
            "requested_by": current_user.username,
            "requested_by_user_id": current_user.id,
            "type": "lead_dead_request",
        },
        created_at=now_utc,
    )
    db.add(notification)
    db.commit()

    enqueue_activity_log(
        background_tasks,
        actor=current_user.username,
        action="REQUEST_DEAD_LEAD",
        details=f"Requested dead mark for lead #{lead.id}",
        lead_id=lead.id,
        metadata={"requested_by_user_id": current_user.id},
    )

    return {
        "status": "requested",
        "lead_id": lead.id,
        **_lead_dead_payload(lead),
    }


@router.post("/leads/{lead_id}/mark-dead")
def mark_lead_dead(
    lead_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {"admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can mark a lead as dead",
        )

    lead = db.query(Lead).options(joinedload(Lead.contact), joinedload(Lead.assignee)).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    now_utc = datetime.utcnow()
    lead.status = "dead"
    lead.lead_status = "unassigned"
    lead.dead_requested = False
    lead.dead_marked_by_user_id = current_user.id
    lead.dead_marked_at = now_utc
    db.commit()
    db.refresh(lead)

    notification = NotificationEvent(
        event_type="lead_marked_dead",
        title="Lead marked dead",
        body=f"{current_user.username} marked lead #{lead.id} as dead.",
        lead_id=lead.id,
        payload={
            "lead_id": lead.id,
            "marked_by": current_user.username,
            "marked_by_user_id": current_user.id,
            "type": "lead_marked_dead",
        },
        created_at=now_utc,
    )
    db.add(notification)
    db.commit()

    enqueue_activity_log(
        background_tasks,
        actor=current_user.username,
        action="MARK_DEAD_LEAD",
        details=f"Marked lead #{lead.id} as dead",
        lead_id=lead.id,
        metadata={"marked_by_user_id": current_user.id},
    )

    return {
        "status": "dead",
        "lead_id": lead.id,
        "lead_status": lead.status,
        **_lead_dead_payload(lead),
    }


@router.delete("/leads/{lead_id}")
def delete_lead_record(
    lead_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {"admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can delete leads",
        )

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    lead_id_value = int(lead.id)
    db.delete(lead)
    db.commit()

    enqueue_activity_log(
        background_tasks,
        actor=current_user.username,
        action="DELETE_LEAD",
        details=f"Deleted lead #{lead_id_value}",
        lead_id=lead_id_value,
        metadata={"deleted_by_user_id": current_user.id},
    )

    return {
        "status": "deleted",
        "lead_id": lead_id_value,
    }


@router.get("/leads/{lead_id}/messages")
def get_lead_messages(lead_id: int, db: Session = Depends(get_db)):
    """
    Fetch the complete chat history for a specific lead.
    Maps exactly to the middle column of the Chat View UI.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found"
        )

    # Query all messages associated with this lead's contact_id
    messages = db.query(Message).filter(
        Message.contact_id == lead.contact_id
    ).order_by(Message.created_at.asc()).all()

    response_data = []
    for msg in messages:
        # Prefer the cleaned text if available, fallback to raw text
        msg_text = msg.text_cleaned if msg.text_cleaned else msg.text_raw
        
        # In case a media message or empty text sneaks through
        if not msg_text:
            msg_text = "📷 [Media/Attachment]"

        response_data.append({
            "id": msg.id,
            "text": msg_text,
            "direction": msg.direction,  # 'inbound' | 'outbound'
            "time": msg.created_at.strftime("%I:%M %p"), # E.g., "10:30 AM"
            "timestamp": msg.created_at.isoformat()
        })

    return response_data


@router.get("/notifications")
def get_notifications(
    limit: int = Query(25, ge=1, le=100, description="Maximum notifications to return"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {"sales_rep", "admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to view notifications",
        )

    rows = (
        db.query(NotificationEvent)
        .order_by(NotificationEvent.created_at.desc(), NotificationEvent.id.desc())
        .limit(limit)
        .all()
    )

    return [
        {
            "id": int(row.id),
            "event_type": row.event_type,
            "title": row.title,
            "body": row.body,
            "lead_id": row.lead_id,
            "payload": row.payload,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "type": "system_notification",
        }
        for row in rows
    ]


@router.post("/leads/{lead_id}/messages/custom")
async def send_custom_message(
    lead_id: int,
    body: CustomMessagePayload,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in {"sales_rep", "admin", "sudo_admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to send messages",
        )

    message_text = (body.message_text or "").strip()
    if not message_text:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="message_text is required",
        )

    lead = db.query(Lead).options(joinedload(Lead.contact)).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    igsid = lead.contact.igsid if lead.contact and lead.contact.igsid else None
    if not igsid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Lead does not have a valid Instagram sender id",
        )

    log_event(
        logger,
        logging.INFO,
        "custom_message.send_attempt",
        lead_id=int(lead_id),
        igsid=str(igsid),
        actor_user_id=int(current_user.id),
        actor_username=str(current_user.username),
        message_length=len(message_text),
    )

    response = automation_mail(str(igsid), message_text=message_text)
    if response is None:
        log_event(
            logger,
            logging.WARNING,
            "custom_message.send_failed",
            lead_id=int(lead_id),
            igsid=str(igsid),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to send message to Instagram",
        )

    log_event(
        logger,
        logging.INFO,
        "custom_message.send_success",
        lead_id=int(lead_id),
        igsid=str(igsid),
        message_id=response.get("message_id") if isinstance(response, dict) else None,
    )

    try:
        msg = append_chat_message(
            db,
            igsid=str(igsid),
            direction="outbound",
            message_text=message_text,
            platform_message_id=(response.get("message_id") if isinstance(response, dict) else None),
            payload=response if isinstance(response, dict) else None,
        )
        db.commit()
        db.refresh(msg)
    except Exception:
        db.rollback()
        raise

    new_message_payload = {
        "id": int(msg.id),
        "text": msg.text_cleaned or msg.text_raw or "📷 [Media/Attachment]",
        "direction": msg.direction,
        "time": msg.created_at.strftime("%I:%M %p") if msg.created_at else None,
        "timestamp": msg.created_at.isoformat() if msg.created_at else None,
        "type": "new_message",
    }
    asyncio.create_task(manager.publish_message(lead_id=int(lead.id), payload=new_message_payload))

    enqueue_activity_log(
        background_tasks,
        actor=current_user.username,
        action="SEND_CUSTOM_MESSAGE",
        details=f"Sent custom message for lead #{lead.id}",
        lead_id=lead.id,
        metadata={"message_id": int(msg.id), "platform_message_id": msg.platform_message_id},
    )

    return {
        "status": "sent",
        "lead_id": int(lead.id),
        "message": new_message_payload,
    }


@router.get("/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db)):
    """
    Aggregates high-level metrics for the main CRM dashboard cards.
    """
    # 1. Total Leads
    total_leads = db.query(Lead).count()

    # 2. Qualified Leads (Count of distinct leads with a LeadSubmitted CAPI event)
    qualified_leads = db.query(MetaConversionEvent.lead_id).filter(
        MetaConversionEvent.event_name == "LeadSubmitted",
        MetaConversionEvent.lead_id.isnot(None)
    ).distinct().count()

    # 3. Converted Leads (Status == 'paid')
    converted_leads = db.query(Lead).filter(Lead.status == "paid").count()

    # 4. Total Revenue (Sum of all paid invoices)
    total_revenue = db.query(func.sum(Invoice.amount)).filter(
        Invoice.status == "paid"
    ).scalar()

    # Handle the case where no invoices exist yet (scalar returns None)
    if total_revenue is None:
        total_revenue = 0.0

    # Calculate conversion rate
    conversion_rate = 0
    if total_leads > 0:
        conversion_rate = round((converted_leads / total_leads) * 100, 1)

    return {
        "totalLeads": total_leads,
        "qualifiedLeads": qualified_leads,
        "convertedLeads": converted_leads,
        "conversionRate": conversion_rate,
        "totalRevenue": float(total_revenue)
    }


@router.get("/dashboard/activity")
def get_dashboard_activity(
    limit: int = Query(10, ge=1, le=50, description="Maximum activity items to return"),
    page: int = Query(1, ge=1, le=50, description="Activity page number"),
    include_meta: bool = Query(False, description="Include pagination metadata"),
    db: Session = Depends(get_db),
):
    """
    Returns a unified recent activity feed for dashboard UI.
    Sources: latest messages, lead submissions, paid invoices, and CAPI events.
    """
    activity_items: list[dict] = []
    offset = (page - 1) * limit
    source_limit = offset + limit + 1

    recent_messages = (
        db.query(Message)
        .options(joinedload(Message.contact))
        .order_by(Message.created_at.desc())
        .limit(source_limit)
        .all()
    )
    for msg in recent_messages:
        contact_igsid = msg.contact.igsid if msg.contact else "unknown"
        preview = (msg.text_cleaned or msg.text_raw or "New message")[:90]
        activity_items.append(
            {
                "type": "message",
                "text": f"{msg.direction.capitalize()} message with {contact_igsid}: {preview}",
                "timestamp": msg.created_at.isoformat() if msg.created_at else None,
            }
        )

    recent_leads = db.query(Lead).order_by(Lead.created_at.desc()).limit(source_limit).all()
    for lead in recent_leads:
        activity_items.append(
            {
                "type": "lead",
                "text": f"Lead #{lead.id} captured",
                "timestamp": lead.created_at.isoformat() if lead.created_at else None,
            }
        )

    recent_paid_invoices = (
        db.query(Invoice)
        .filter(Invoice.status == "paid")
        .order_by(Invoice.paid_at.desc().nullslast(), Invoice.created_at.desc())
        .limit(source_limit)
        .all()
    )
    for invoice in recent_paid_invoices:
        paid_time = invoice.paid_at or invoice.created_at
        amount_display = f"{float(invoice.amount):,.2f}"
        activity_items.append(
            {
                "type": "conversion",
                "text": f"Invoice {invoice.stripe_invoice_id} paid (${amount_display})",
                "timestamp": paid_time.isoformat() if paid_time else None,
            }
        )

    recent_capi_events = (
        db.query(MetaConversionEvent)
        .filter(MetaConversionEvent.event_name == "LeadSubmitted")
        .order_by(MetaConversionEvent.created_at.desc())
        .limit(source_limit)
        .all()
    )
    for event in recent_capi_events:
        activity_items.append(
            {
                "type": "qualified",
                "text": f"LeadSubmitted synced for lead #{event.lead_id}" if event.lead_id else "LeadSubmitted synced",
                "timestamp": event.created_at.isoformat() if event.created_at else None,
            }
        )

    def _timestamp_sort_key(item: dict) -> datetime:
        ts = item.get("timestamp")
        if not ts:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    activity_items.sort(key=_timestamp_sort_key, reverse=True)

    paged_items = activity_items[offset: offset + limit]
    has_next = len(activity_items) > offset + limit

    if include_meta:
        return {
            "items": paged_items,
            "page": page,
            "limit": limit,
            "hasNext": has_next,
        }

    return paged_items


@router.post("/leads/{lead_id}/payments/custom")
def create_custom_lead_payment(
    lead_id: int,
    body: CustomLeadPaymentPayload,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    amount_input = body.amount
    if amount_input <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="amount must be greater than 0",
        )

    currency = (body.currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="currency must be a 3-letter ISO code",
        )

    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    _ensure_lead_payment_access(lead=lead, current_user=current_user)

    try:
        normalized_amount = Decimal(str(amount_input)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid amount",
        )

    if normalized_amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="amount must be greater than 0",
        )

    created_at = datetime.utcnow()
    manual_invoice_id = f"manual-invoice-{lead_id}-{uuid.uuid4().hex[:12]}"
    manual_payment_event_id = f"manual-payment-{lead_id}-{uuid.uuid4().hex[:12]}"

    try:
        invoice = Invoice(
            lead_id=lead.id,
            stripe_invoice_id=manual_invoice_id,
            stripe_customer_id=None,
            amount=normalized_amount,
            currency=currency.lower(),
            status="paid",
            created_at=created_at,
            paid_at=created_at,
        )
        db.add(invoice)
        db.flush()

        payment_event = PaymentEvent(
            invoice_id=invoice.id,
            stripe_event_id=manual_payment_event_id,
            amount=normalized_amount,
            event_type="custom.payment",
            capi_sent=False,
            capi_event_id=None,
            created_at=created_at,
        )
        db.add(payment_event)
        db.flush()

        meta_event = persist_purchase_event_for_lead(
            db,
            lead_id=lead.id,
            value=float(normalized_amount),
            currency=currency,
            email=lead.email,
            phone=lead.phone,
        )
        db.flush()

        payment_event.capi_event_id = str(meta_event.id)

        lead.status = "paid"
        if lead.converted_at is None:
            lead.converted_at = created_at

        db.commit()
        db.refresh(meta_event)
        db.refresh(invoice)
        db.refresh(payment_event)
        db.refresh(lead)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception:
        db.rollback()
        raise

    task_id = None
    if body.send_now:
        task = celery_app.send_task(
            "tasks.post_meta_conversion_event",
            kwargs={"event_id": int(meta_event.id)},
        )
        task_id = task.id

    enqueue_activity_log(
        background_tasks,
        actor=current_user.username,
        action="CREATE_PAYMENT",
        details=f"Created custom payment for lead #{lead.id} amount={float(normalized_amount):.2f} {currency}",
        lead_id=lead.id,
        metadata={
            "invoice_id": invoice.id,
            "payment_event_id": payment_event.id,
            "meta_event_id": int(meta_event.id),
            "queued_for_meta": bool(body.send_now),
        },
    )

    return {
        "status": "created",
        "lead_id": lead.id,
        "invoice_id": invoice.id,
        "invoice_reference": invoice.stripe_invoice_id,
        "invoice_status": invoice.status,
        "payment_event_id": payment_event.id,
        "payment_reference": payment_event.stripe_event_id,
        "payment_event_type": payment_event.event_type,
        "payment_capi_sent": payment_event.capi_sent,
        "payment_capi_event_id": payment_event.capi_event_id,
        "payment_created_at": payment_event.created_at.isoformat() if payment_event.created_at else None,
        "amount": float(normalized_amount),
        "currency": currency,
        "meta_event_id": int(meta_event.id),
        "event_name": meta_event.event_name,
        "queued_for_meta": bool(body.send_now),
        "task_id": task_id,
    }


@router.get("/leads/{lead_id}/payments")
def get_lead_payments(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    _ensure_lead_payment_access(lead=lead, current_user=current_user)

    invoices = (
        db.query(Invoice)
        .filter(Invoice.lead_id == lead_id)
        .order_by(Invoice.paid_at.desc().nullslast(), Invoice.created_at.desc(), Invoice.id.desc())
        .all()
    )

    invoice_ids = [invoice.id for invoice in invoices]
    payment_events_by_invoice: dict[int, list[PaymentEvent]] = {invoice_id: [] for invoice_id in invoice_ids}
    if invoice_ids:
        payment_events = (
            db.query(PaymentEvent)
            .filter(PaymentEvent.invoice_id.in_(invoice_ids))
            .order_by(PaymentEvent.created_at.desc(), PaymentEvent.id.desc())
            .all()
        )
        for payment_event in payment_events:
            payment_events_by_invoice.setdefault(payment_event.invoice_id, []).append(payment_event)

    items = []
    total_paid_amount = Decimal("0.00")

    for invoice in invoices:
        if invoice.status == "paid":
            total_paid_amount += Decimal(str(invoice.amount or 0))

        invoice_payment_events = payment_events_by_invoice.get(invoice.id, [])
        items.append(
            {
                "invoice": {
                    "id": invoice.id,
                    "reference": invoice.stripe_invoice_id,
                    "status": invoice.status,
                    "amount": float(invoice.amount),
                    "currency": (invoice.currency or "").upper(),
                    "created_at": invoice.created_at.isoformat() if invoice.created_at else None,
                    "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
                },
                "payments": [
                    {
                        "id": payment_event.id,
                        "reference": payment_event.stripe_event_id,
                        "event_type": payment_event.event_type,
                        "amount": float(payment_event.amount),
                        "capi_sent": payment_event.capi_sent,
                        "capi_event_id": payment_event.capi_event_id,
                        "created_at": payment_event.created_at.isoformat() if payment_event.created_at else None,
                    }
                    for payment_event in invoice_payment_events
                ],
            }
        )

    return {
        "lead_id": lead.id,
        "invoice_count": len(invoices),
        "total_paid_amount": float(total_paid_amount),
        "items": items,
    }

@router.websocket("/ws/leads/{lead_id}")
async def websocket_endpoint(websocket: WebSocket, lead_id: int):
    # Note: In production, you'll want to extract the auth token from query params 
    # or headers here to verify `get_current_user` logic.
    await manager.connect(websocket, lead_id)
    try:
        while True:
            # We just keep the connection alive. The client doesn't need to send anything
            # because the server pushes updates via Redis.
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, lead_id)


@router.websocket("/ws/notifications")
async def notifications_websocket_endpoint(websocket: WebSocket):
    await manager.connect_notifications(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_notifications(websocket)