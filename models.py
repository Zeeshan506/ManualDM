from datetime import datetime

from sqlalchemy import Boolean, JSON, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from database import Base


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    object = Column(String, nullable=True)
    status = Column(String, nullable=False)
    raw_payload = Column(JSON, nullable=True)
    raw_body = Column(Text, nullable=True)
    fingerprint = Column(String, nullable=True, index=True)


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    instagram_user_id = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    referral_id = Column(Text, nullable=True, index=True)
    flow_step = Column(String, nullable=False, default="new")
    status = Column(String, nullable=False, default="new")
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    last_message_at = Column(DateTime(timezone=True), nullable=True)

    inbound_messages = relationship("InboundMessage", back_populates="lead")
    chat_messages = relationship("ChatMessage", back_populates="lead", cascade="all, delete-orphan")


class InboundMessage(Base):
    __tablename__ = "inbound_messages"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    instagram_user_id = Column(String, nullable=False, index=True)
    platform_message_id = Column(String, nullable=True, index=True)
    text_raw = Column(Text, nullable=True)
    text_cleaned = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    processed = Column(Boolean, nullable=False, default=False, index=True)
    received_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="inbound_messages")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    instagram_user_id = Column(String, nullable=False, index=True)
    direction = Column(String, nullable=False, index=True)  # inbound | outbound
    message_text = Column(Text, nullable=True)
    platform_message_id = Column(String, nullable=True, index=True)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    lead = relationship("Lead", back_populates="chat_messages")