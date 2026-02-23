from fastapi import Depends, HTTPException, Query, status, APIRouter
from sqlalchemy.orm import joinedload, Session
from database import get_db
from sqlalchemy import func
from models import Lead, Message, Contact, Invoice, MetaConversionEvent

router = APIRouter(prefix="/api", tags=["API Endpoints"])

@router.get("/leads")
def get_all_leads(
    status: str | None = Query(None, description="Filter leads by status (e.g., new, invoiced, paid, cancelled)"),
    db: Session = Depends(get_db)
):
    """
    Fetch all leads for the CRM directory.
    Joins with the Contact table to get the Instagram ID and last message timestamp.
    """
    # Start building the query, joining Lead with Contact to avoid N+1 query issues
    query = db.query(Lead).options(joinedload(Lead.contact))

    # Apply optional status filter
    if status and status.lower() != "all":
        query = query.filter(Lead.status == status.lower())

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
            # Note: Your schema doesn't currently store the user's real name. 
            # You can extract this from the IG webhook user profile payload later if needed.
            "name": "", 
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
        "name": "",  # Placeholder until IG profile scraping is added
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