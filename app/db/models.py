from datetime import datetime
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Numeric,
    JSON,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False)
    event_type = Column(String, nullable=True)
    external_event_id = Column(String, nullable=True, index=True)
    idempotency_key = Column(String, nullable=False, unique=True, index=True)
    payload = Column(JSON, nullable=True)
    processed = Column(Boolean, default=False, nullable=False)
    processing_state = Column(String, nullable=False, default="received", index=True)
    enqueue_status = Column(String, nullable=False, default="pending", index=True)
    enqueue_attempts = Column(Integer, nullable=False, default=0)
    processing_attempts = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)
    queued_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_webhook_source_external", "source", "external_event_id"),
        Index("ix_webhook_enqueue_next_retry", "enqueue_status", "next_retry_at"),
    )


class Contact(Base):
    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True)
    igsid = Column(String, nullable=False, unique=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, nullable=False)

    referral_id = Column(String, nullable=True, index=True)
    first_event_id = Column(String, nullable=True)
    first_event_name = Column(String, nullable=True)

    platform = Column(String, nullable=False, default="instagram")

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    messages = relationship("Message", back_populates="contact", cascade="all, delete-orphan")
    lead = relationship("Lead", back_populates="contact", uselist=False)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)

    contact_id = Column(
        Integer,
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    direction = Column(String, nullable=False, index=True)

    text_raw = Column(Text, nullable=True)
    text_cleaned = Column(Text, nullable=True)

    platform_message_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=True)

    processed = Column(Boolean, default=False, nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    contact = relationship("Contact", back_populates="messages")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True, index=True)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=True)
    role = Column(
        Enum("sudo_admin", "admin", "sales_rep", name="user_role"),
        nullable=False,
        default="sales_rep",
    )
    is_active = Column(Boolean, nullable=False, default=True)

    assigned_leads = relationship(
        "Lead",
        back_populates="assignee",
        foreign_keys="Lead.assigned_to",
    )
    dead_requested_leads = relationship(
        "Lead",
        foreign_keys="Lead.dead_requested_by_user_id",
    )
    dead_marked_leads = relationship(
        "Lead",
        foreign_keys="Lead.dead_marked_by_user_id",
    )


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
    name = Column(String, nullable=True)
    assigned_to = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    lead_status = Column(String, nullable=False, default="unassigned")

    status = Column(String, nullable=False, default="new")
    dead_requested = Column(Boolean, nullable=False, default=False, index=True)
    dead_requested_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    dead_requested_at = Column(DateTime(timezone=True), nullable=True)
    dead_marked_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    dead_marked_at = Column(DateTime(timezone=True), nullable=True)
    referral_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    converted_at = Column(DateTime(timezone=True), nullable=True)

    contact = relationship("Contact", back_populates="lead")
    assignee = relationship(
        "User",
        back_populates="assigned_leads",
        foreign_keys=[assigned_to],
    )
    dead_requested_by = relationship(
        "User",
        foreign_keys=[dead_requested_by_user_id],
    )
    dead_marked_by = relationship(
        "User",
        foreign_keys=[dead_marked_by_user_id],
    )
    invoices = relationship("Invoice", back_populates="lead", cascade="all, delete-orphan")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True)

    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="CASCADE"), nullable=False, index=True)

    stripe_invoice_id = Column(String, nullable=False, unique=True, index=True)
    stripe_customer_id = Column(String, nullable=True, index=True)

    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String, nullable=False, default="usd")

    status = Column(String, nullable=False, default="draft")

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    lead = relationship("Lead", back_populates="invoices")
    payment_events = relationship("PaymentEvent", back_populates="invoice", cascade="all, delete-orphan")


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
    event_type = Column(String, nullable=False)

    capi_sent = Column(Boolean, default=False, nullable=False)
    capi_event_id = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    invoice = relationship("Invoice", back_populates="payment_events")


class MetaConversionEvent(Base):
    __tablename__ = "meta_conversion_events"

    id = Column(Integer, primary_key=True)

    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)

    event_name = Column(String, nullable=False, index=True)
    event_time = Column(Integer, nullable=False)
    event_id = Column(String, nullable=True, index=True)
    action_source = Column(String, nullable=True)
    messaging_channel = Column(String, nullable=True)

    user_data = Column(JSON, nullable=True)
    custom_data = Column(JSON, nullable=True)

    partner_agent = Column(String, nullable=True)
    full_payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    lead = relationship("Lead", backref="meta_conversion_events")


class ActivityLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)

    action_type = Column(String, nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_username = Column(String, nullable=True, index=True)
    actor_role = Column(String, nullable=True, index=True)
    entity_type = Column(String, nullable=True, index=True)
    entity_id = Column(String, nullable=True, index=True)
    details = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)


class NotificationEvent(Base):
    __tablename__ = "notification_events"

    id = Column(Integer, primary_key=True)

    event_type = Column(String, nullable=False, index=True)
    title = Column(String, nullable=False)
    body = Column(Text, nullable=True)

    lead_id = Column(Integer, ForeignKey("leads.id", ondelete="SET NULL"), nullable=True, index=True)
    payload = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False, index=True)

    lead = relationship("Lead", backref="notification_events")
