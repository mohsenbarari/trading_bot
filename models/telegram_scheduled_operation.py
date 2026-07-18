"""Foreign-local durable sources for bounded scheduled Telegram operations."""
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


class TelegramScheduledOperation(Base):
    """Authoritative intent for low-priority market and cleanup side effects.

    The row is deliberately NO_SYNC execution state. Producers are restricted
    to the foreign Telegram runtime and the shared queue is the only Bot API
    consumer while queue-v1 owns delivery.
    """

    __tablename__ = "telegram_scheduled_operations"
    __table_args__ = (
        UniqueConstraint(
            "dedupe_key",
            name="ux_telegram_scheduled_operations_dedupe_key",
        ),
        Index(
            "ix_telegram_scheduled_operations_due",
            "due_at",
            "id",
            postgresql_where=text(
                "status = 'pending' AND queue_job_id IS NULL "
                "AND reconciliation_required_at IS NULL"
            ),
        ),
        Index(
            "ux_telegram_scheduled_operations_queue_job",
            "queue_job_id",
            unique=True,
            postgresql_where=text("queue_job_id IS NOT NULL"),
        ),
        Index("ix_telegram_scheduled_operations_run", "run_id", "status"),
        CheckConstraint(
            "action_kind IN ('noncritical_market', 'temporary_cleanup', "
            "'cosmetic_cleanup')",
            name="ck_telegram_scheduled_operations_action",
        ),
        CheckConstraint(
            "method IN ('sendMessage', 'deleteMessage', "
            "'editMessageReplyMarkup')",
            name="ck_telegram_scheduled_operations_method",
        ),
        CheckConstraint(
            "destination_class IN ('private', 'channel')",
            name="ck_telegram_scheduled_operations_destination_class",
        ),
        CheckConstraint(
            "status IN ('pending', 'sent', 'skipped', 'terminal_failed', "
            "'cancelled')",
            name="ck_telegram_scheduled_operations_status",
        ),
        CheckConstraint(
            "source_version > 0",
            name="ck_telegram_scheduled_operations_source_version",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_scheduled_operations_attempt_count",
        ),
        CheckConstraint(
            "((queue_job_id IS NULL AND queue_handed_off_at IS NULL) OR "
            "(queue_job_id IS NOT NULL AND queue_handed_off_at IS NOT NULL))",
            name="ck_telegram_scheduled_operations_queue_binding",
        ),
        CheckConstraint(
            "NOT (queue_job_id IS NOT NULL AND "
            "reconciliation_required_at IS NOT NULL)",
            name="ck_telegram_scheduled_operations_queue_owner",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    dedupe_key = Column(String(192), nullable=False)
    action_kind = Column(String(40), nullable=False)
    source_id = Column(String(120), nullable=False)
    source_version = Column(BigInteger, nullable=False, default=1, server_default="1")
    recipient_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    destination_class = Column(String(16), nullable=False)
    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)
    method = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    payload_hash = Column(String(64), nullable=False)
    template_version = Column(String(64), nullable=False)
    due_at = Column(DateTime(timezone=True), nullable=False)
    freshness_deadline_at = Column(DateTime(timezone=True), nullable=True)
    run_id = Column(String(192), nullable=True)
    scope_allowed = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    status = Column(String(32), nullable=False, default="pending", server_default="pending")
    attempt_count = Column(Integer, nullable=False, default=0, server_default="0")
    queue_job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    queue_handed_off_at = Column(DateTime(timezone=True), nullable=True)
    reconciliation_required_at = Column(DateTime(timezone=True), nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    reason = Column(String(160), nullable=True)
    last_error_class = Column(String(120), nullable=True)
    last_error_message = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
