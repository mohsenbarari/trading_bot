"""Durable, foreign-local control state for Telegram channel resume.

This table is the audit and crash-recovery boundary between PostgreSQL pause
evidence, Telegram preflight, and the Redis destination gate.  It is execution
state and must never be synchronized to Iran.
"""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
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


ACTIVE_TELEGRAM_DELIVERY_RESUME_STATES = (
    "requested",
    "database_applied",
    "redis_applied",
)


class TelegramDeliveryResumeOperation(Base):
    __tablename__ = "telegram_delivery_resume_operations"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            name="ux_telegram_delivery_resume_operations_request_id",
        ),
        CheckConstraint(
            "scope = 'channel_destination'",
            name="ck_telegram_delivery_resume_operations_scope",
        ),
        CheckConstraint(
            "state IN ('requested', 'database_applied', 'redis_applied', "
            "'completed', 'failed')",
            name="ck_telegram_delivery_resume_operations_state",
        ),
        CheckConstraint(
            "length(request_id) BETWEEN 16 AND 128",
            name="ck_telegram_delivery_resume_operations_request_id",
        ),
        CheckConstraint(
            "length(requested_by) BETWEEN 1 AND 128",
            name="ck_telegram_delivery_resume_operations_requested_by",
        ),
        CheckConstraint(
            "length(pause_evidence_hash) = 64",
            name="ck_telegram_delivery_resume_operations_evidence_hash",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_delivery_resume_operations_attempt_count",
        ),
        CheckConstraint(
            "((state = 'requested' AND db_applied_at IS NULL AND "
            "redis_applied_at IS NULL AND completed_at IS NULL) OR "
            "(state = 'failed' AND db_applied_at IS NULL AND "
            "redis_applied_at IS NULL AND completed_at IS NULL AND "
            "failure_class IS NOT NULL) OR "
            "(state = 'database_applied' AND db_applied_at IS NOT NULL AND "
            "redis_applied_at IS NULL AND completed_at IS NULL) OR "
            "(state = 'redis_applied' AND db_applied_at IS NOT NULL AND "
            "redis_applied_at IS NOT NULL AND completed_at IS NULL) OR "
            "(state = 'completed' AND db_applied_at IS NOT NULL AND "
            "redis_applied_at IS NOT NULL AND completed_at IS NOT NULL))",
            name="ck_telegram_delivery_resume_operations_phase_timestamps",
        ),
        Index(
            "ux_telegram_delivery_resume_active_destination",
            "destination_key",
            unique=True,
            postgresql_where=text(
                "state IN ('requested', 'database_applied', 'redis_applied')"
            ),
        ),
        Index(
            "ix_telegram_delivery_resume_operations_state",
            "state",
            "updated_at",
            "id",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(String(128), nullable=False)
    scope = Column(
        String(32),
        nullable=False,
        default="channel_destination",
        server_default=text("'channel_destination'"),
    )
    destination_key = Column(String(256), nullable=False)
    bot_identities = Column(JSON, nullable=False)
    pause_job_ids = Column(JSON, nullable=False)
    pause_evidence_hash = Column(String(64), nullable=False)
    requested_by = Column(String(128), nullable=False)
    attempt_history = Column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'::json"),
    )
    preflight_evidence = Column(JSON, nullable=True)
    state = Column(
        String(32),
        nullable=False,
        default="requested",
        server_default=text("'requested'"),
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    failure_class = Column(String(160), nullable=True)
    failure_detail = Column(Text, nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    preflight_completed_at = Column(DateTime(timezone=True), nullable=True)
    db_applied_at = Column(DateTime(timezone=True), nullable=True)
    redis_applied_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
