"""Durable, foreign-local inbox for Telegram provider outcomes.

Provider facts are immutable once inserted.  The apply lifecycle is mutable so
domain feedback can be retried without calling Telegram a second time.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import func

from .database import Base


TELEGRAM_PROVIDER_OUTCOME_PENDING = "pending"
TELEGRAM_PROVIDER_OUTCOME_APPLIED = "applied"
TELEGRAM_PROVIDER_OUTCOME_QUARANTINED = "quarantined"


class TelegramDeliveryProviderOutcomeRecord(Base):
    __tablename__ = "telegram_delivery_provider_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "lease_token",
            name="ux_telegram_delivery_provider_outcomes_fence",
        ),
        CheckConstraint(
            "lease_token > 0",
            name="ck_telegram_delivery_provider_outcomes_lease_token",
        ),
        CheckConstraint(
            "apply_attempt_count >= 0",
            name="ck_telegram_delivery_provider_outcomes_apply_attempts",
        ),
        CheckConstraint(
            "apply_state IN ('pending', 'applied', 'quarantined')",
            name="ck_telegram_delivery_provider_outcomes_apply_state",
        ),
        CheckConstraint(
            "transport_phase IS NULL OR transport_phase IN "
            "('pre_write', 'write_unknown', 'response_received')",
            name="ck_telegram_delivery_provider_outcomes_transport_phase",
        ),
        Index(
            "ix_telegram_delivery_provider_outcomes_pending",
            "next_apply_at",
            "created_at",
            "id",
            postgresql_where=text("apply_state = 'pending'"),
        ),
        Index(
            "ix_telegram_delivery_provider_outcomes_job",
            "job_id",
            "created_at",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    lease_token = Column(BigInteger, nullable=False)
    worker_id = Column(String(128), nullable=False)
    bot_identity = Column(String(128), nullable=False)
    method = Column(String(64), nullable=False)
    transport_phase = Column(String(24), nullable=True)
    gateway_ok = Column(Boolean, nullable=False)
    provider_status_code = Column(Integer, nullable=True)
    provider_response = Column(JSON, nullable=True)
    provider_error_class = Column(String(120), nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    retry_after_seconds = Column(BigInteger, nullable=True)
    outcome_hash = Column(String(64), nullable=False)
    apply_state = Column(
        String(24),
        nullable=False,
        default=TELEGRAM_PROVIDER_OUTCOME_PENDING,
        server_default=text("'pending'"),
    )
    apply_attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_apply_at = Column(DateTime(timezone=True), nullable=True)
    applied_at = Column(DateTime(timezone=True), nullable=True)
    last_apply_error_class = Column(String(120), nullable=True)
    last_apply_error_message = Column(Text, nullable=True)
    payload_redacted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
