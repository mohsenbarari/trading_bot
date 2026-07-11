"""Local exactly-once receipt for cross-server User counter events."""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, JSON, String, text
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
        CheckConstraint(
            "event_kind IN ('increment', 'reset')",
            name="ck_user_counter_event_receipts_known_kind",
        ),
        CheckConstraint(
            "event_epoch >= 1",
            name="ck_user_counter_event_receipts_epoch_positive",
        ),
        CheckConstraint(
            "outcome IN ('applied', 'excluded_pre_boundary')",
            name="ck_user_counter_event_receipts_known_outcome",
        ),
        Index(
            "ix_user_counter_event_receipts_user_period",
            "user_id",
            "event_kind",
            "occurred_at",
            "event_epoch",
        ),
        Index(
            "ux_user_counter_event_receipts_user_reset_epoch",
            "user_id",
            "event_epoch",
            unique=True,
            postgresql_where=text("event_kind = 'reset'"),
        ),
    )

    event_id = Column(UUID(as_uuid=True), primary_key=True)
    source_server = Column(String(16), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_hash = Column(String(64), nullable=False)
    event_kind = Column(String(16), nullable=False)
    event_epoch = Column(BigInteger, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    deltas = Column(JSON, nullable=False, default=dict)
    outcome = Column(String(32), nullable=False, default="applied", server_default=text("'applied'"))
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
