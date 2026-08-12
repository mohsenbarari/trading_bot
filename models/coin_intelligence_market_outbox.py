"""Durable, privacy-minimized product-event outbox for market intelligence."""

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Index, Integer, JSON, String
from sqlalchemy.sql import func

from .database import Base


class CoinIntelligenceMarketOutbox(Base):
    """One committed Offer/Trade market fact awaiting an independent consumer.

    The payload has only economic fields.  User identity, mobile number,
    free-text notes, Telegram IDs and raw text are intentionally absent.
    """

    __tablename__ = "coin_intelligence_market_outbox"
    __table_args__ = (
        CheckConstraint(
            "event_kind IN "
            "('OFFER_OPENED', 'OFFER_PARTIAL', 'OFFER_COMPLETED', "
            "'OFFER_CANCELLED', 'OFFER_EXPIRED', 'TRADE_COMPLETED')",
            name="ck_coin_intelligence_market_outbox_event_kind",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'PROCESSING', 'COMPLETE', 'FAILED')",
            name="ck_coin_intelligence_market_outbox_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_coin_intelligence_market_outbox_attempts"),
        Index(
            "ix_coin_intelligence_market_outbox_claim",
            "status",
            "available_at_utc",
            "created_at",
        ),
        Index(
            "ix_coin_intelligence_market_outbox_subject",
            "subject_kind",
            "subject_id",
            "occurred_at_utc",
        ),
    )

    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(64), nullable=False, unique=True, index=True)
    event_kind = Column(String(32), nullable=False)
    subject_kind = Column(String(16), nullable=False)
    subject_id = Column(Integer, nullable=False)
    occurred_at_utc = Column(DateTime(timezone=True), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    available_at_utc = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_token = Column(String(64), nullable=True)
    lease_expires_at_utc = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(96), nullable=True)
    completed_at_utc = Column(DateTime(timezone=True), nullable=True)
    model_eligible = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
