from fastapi import Depends, HTTPException, Query, status, APIRouter
from sqlalchemy.orm import joinedload, Session
from database import get_db
from sqlalchemy import func
from datetime import datetime, timezone
from dependencies import get_current_user
from models import Lead, Message, Contact, Invoice, MetaConversionEvent, User

router = APIRouter(prefix="/api", tags=["API Endpoints"])

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
    # Start building the query, joining Lead with Contact to avoid N+1 query issues
    query = db.query(Lead).options(joinedload(Lead.contact))

    normalized_status = status_filter.lower() if status_filter else None
    is_unassigned_filter = normalized_status == "unassigned"

    if current_user.role == "sales_rep":
        if is_unassigned_filter:
            query = query.filter(Lead.assigned_to.is_(None))
        else:
            target_assignee = current_user.id if assigned_to is None else assigned_to
            if target_assignee != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Sales reps can only view their own assigned chats",
                )
            query = query.filter(Lead.assigned_to == current_user.id)
    elif assigned_to is not None:
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
            "lastActive": last_active.isoformat() if last_active else None
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Assign a lead to the current authenticated user.
    Moves lead_status from 'unassigned' to 'active'.
    """
    lead = db.query(Lead).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Lead {lead_id} not found",
        )

    lead.assigned_to = current_user.id
    if lead.lead_status == "unassigned":
        lead.lead_status = "active"

    db.commit()
    db.refresh(lead)

    return {
        "id": lead.id,
        "assigned_to": lead.assigned_to,
        "lead_status": lead.lead_status,
    }


@router.get("/leads/{lead_id}")
def get_lead_details(lead_id: int, db: Session = Depends(get_db)):
    """
    Fetch specific lead details for the right-hand panel in the Chat View.
    Also checks if the LeadSubmitted CAPI event has been fired.
    """
    lead = db.query(Lead).options(joinedload(Lead.contact)).filter(Lead.id == lead_id).first()
    
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
        "createdAt": lead.created_at.isoformat()
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
    db: Session = Depends(get_db),
):
    """
    Returns a unified recent activity feed for dashboard UI.
    Sources: latest messages, lead submissions, paid invoices, and CAPI events.
    """
    activity_items: list[dict] = []

    recent_messages = (
        db.query(Message)
        .options(joinedload(Message.contact))
        .order_by(Message.created_at.desc())
        .limit(limit)
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

    recent_leads = db.query(Lead).order_by(Lead.created_at.desc()).limit(limit).all()
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
        .limit(limit)
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
        .limit(limit)
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
    return activity_items[:limit]