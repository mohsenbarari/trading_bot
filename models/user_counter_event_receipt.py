"""Local exactly-once receipt for cross-server User counter events."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .database import Base


class UserCounterEventReceipt(Base):
    __tablename__ = "user_counter_event_receipts"
    __table_args__ = (
        CheckConstraint(
            "source_server IN ('iran', 'foreign')",
            name="ck_user_counter_event_receipts_known_source",
        ),
        CheckConstraint(
            "length(event_hash) = 64",
            name="ck_user_counter_event_receipts_event_hash",
        ),
    )

    event_id = Column(UUID(as_uuid=True), primary_key=True)
    source_server = Column(String(16), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_hash = Column(String(64), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
