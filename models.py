from datetime import datetime
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Numeric,
    JSON,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


# -----------------------------
# 1️⃣ Webhook Events (Raw Logging)
# -----------------------------


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)  # stripe | instagram | meta
    event_type = Column(String, nullable=True)
    external_event_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=True)
    processed = Column(Boolean, default=False, nullable=False)
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_webhook_source_external", "source", "external_event_id"),
    )


# -----------------------------
# 2️⃣ Contact (Created on First DM)
# -----------------------------

class Contact(Base):

    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)

    # Instagram sender ID
    igsid = Column(String, nullable=False, unique=True, index=True)

    # Internal tracking UUID
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False)

    # Attribution / Tracking
    referral_id = Column(String, nullable=True, index=True)
    first_event_id = Column(String, nullable=True)
    first_event_name = Column(String, nullable=True)

    platform = Column(String, nullable=False, default="instagram")

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    messages = relationship(
        "Message", back_populates="contact", cascade="all, delete-orphan"
    )
    lead = relationship("Lead", back_populates="contact", uselist=False)


# -----------------------------
# 3️⃣ Messages (ALL DMs)
# -----------------------------


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)

    contact_id = Column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    direction = Column(String, nullable=False, index=True)  # inbound | outbound

    text_raw = Column(Text, nullable=True)
    text_cleaned = Column(Text, nullable=True)

    platform_message_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=True)

    processed = Column(Boolean, default=False, nullable=False, index=True)

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    contact = relationship("Contact", back_populates="messages")


# -----------------------------
# 4️⃣ Lead (Created When Qualified)
# -----------------------------



class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True)

    contact_id = Column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)

    status = Column(String, nullable=False, default="new")
    # new | invoiced | paid | cancelled

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    converted_at = Column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact", back_populates="lead")
    invoices = relationship(
        "Invoice", back_populates="lead", cascade="all, delete-orphan"
    )


# -----------------------------
# 5️⃣ Invoice (Stripe Mapping)
# -----------------------------


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)

    lead_id = Column(
        Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True
    )

    stripe_invoice_id = Column(String, nullable=False, unique=True, index=True)
    stripe_customer_id = Column(String, nullable=True, index=True)

    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String, nullable=False, default="usd")

    status = Column(String, nullable=False, default="draft")
    # draft | sent | paid | failed

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )
    paid_at = Column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead", back_populates="invoices")
    payment_events = relationship(
        "PaymentEvent", back_populates="invoice", cascade="all, delete-orphan"
    )


# -----------------------------
# 6️⃣ Payment Events (Stripe Webhook + CAPI Control)
# -----------------------------


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id = Column(Integer, primary_key=True)

    invoice_id = Column(
        Integer,
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    stripe_event_id = Column(String, nullable=False, unique=True, index=True)

    amount = Column(Numeric(10, 2), nullable=False)
    event_type = Column(String, nullable=False)  # invoice.paid

    capi_sent = Column(Boolean, default=False, nullable=False)
    capi_event_id = Column(String, nullable=True)

    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, nullable=False
    )

    invoice = relationship("Invoice", back_populates="payment_events")


# -----------------------------
# 7️⃣ Meta Conversion Events (Custom Events)
# -----------------------------


class MetaConversionEvent(Base):
    __tablename__ = "meta_conversion_events"

    id = Column(Integer, primary_key=True)

    # optional relation to internal Lead
    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)

    # Core event fields (mirror payload)
    event_name = Column(String, nullable=False, index=True)           # e.g. "Purchase"
    event_time = Column(Integer, nullable=False)                       # unix timestamp
    action_source = Column(String, nullable=True)                      # e.g. "business_messaging"
    messaging_channel = Column(String, nullable=True)                  # e.g. "instagram"

    # Raw structured parts of the payload
    user_data = Column(JSON, nullable=True)
    custom_data = Column(JSON, nullable=True)

    # Top-level partner agent field
    partner_agent = Column(String, nullable=True)

    # Store the full payload for audit/debug
    full_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # relationships
    lead = relationship("Lead", backref="meta_conversion_events")
