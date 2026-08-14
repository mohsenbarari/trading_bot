"""Local receipt for Telegram market open/close channel notices."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import func

from .database import Base


class MarketChannelNoticeReceipt(Base):
    """Foreign-local idempotency record for market channel notices.

    This table is intentionally not part of cross-server sync. It records the
    Telegram side effect that only the foreign server is allowed to execute.
    """

    __tablename__ = "market_channel_notice_receipts"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="ux_market_channel_notice_receipts_dedupe_key"),
        Index(
            "ix_market_channel_notice_receipts_transition",
            "transition",
            "transition_at",
        ),
        Index(
            "ix_market_channel_notice_receipts_status",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_market_channel_notice_receipts_queue_handoff",
            "next_retry_at",
            "id",
            postgresql_where=text(
                "status IN ('pending', 'failed') "
                "AND queue_job_id IS NULL "
                "AND queue_reconciliation_required_at IS NULL"
            ),
        ),
        Index(
            "ux_market_channel_notice_receipts_queue_job",
            "queue_job_id",
            unique=True,
            postgresql_where=text("queue_job_id IS NOT NULL"),
        ),
        CheckConstraint(
            "((queue_job_id IS NULL AND queue_handed_off_at IS NULL) OR "
            "(queue_job_id IS NOT NULL AND queue_handed_off_at IS NOT NULL))",
            name="ck_market_channel_notice_receipts_queue_binding",
        ),
        CheckConstraint(
            "NOT (queue_job_id IS NOT NULL AND "
            "queue_reconciliation_required_at IS NOT NULL)",
            name="ck_market_channel_notice_receipts_queue_owner",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    dedupe_key = Column(String(160), nullable=False)
    transition = Column(String(16), nullable=False)
    transition_at = Column(DateTime(timezone=True), nullable=False)
    notice_text = Column(String(240), nullable=False)
    channel_id = Column(String(80), nullable=True)
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    telegram_message_id = Column(Integer, nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    last_error_class = Column(String(120), nullable=True)
    last_error = Column(Text, nullable=True)
    queue_job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    queue_handed_off_at = Column(DateTime(timezone=True), nullable=True)
    queue_reconciliation_required_at = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
