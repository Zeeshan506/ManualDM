from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

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
# *** End Patch