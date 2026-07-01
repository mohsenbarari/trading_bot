"""Generic Telegram private-message outbox.

Rows in this table are durable operational delivery intents. They may be
created on either server, synced to the foreign server, and consumed only by
the Telegram bot runtime on the foreign server.
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
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


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


class TelegramNotificationOutboxStatus(str, enum.Enum):
    PENDING = "pending"
    SENDING = "sending"
    RETRYABLE_FAILED = "retryable_failed"
    SENT = "sent"
    SKIPPED = "skipped"
    TERMINAL_FAILED = "terminal_failed"


TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES = {
    TelegramNotificationOutboxStatus.SENT.value,
    TelegramNotificationOutboxStatus.SKIPPED.value,
    TelegramNotificationOutboxStatus.TERMINAL_FAILED.value,
}


NON_TERMINAL_TELEGRAM_NOTIFICATION_OUTBOX_STATUSES = {
    TelegramNotificationOutboxStatus.PENDING.value,
    TelegramNotificationOutboxStatus.SENDING.value,
    TelegramNotificationOutboxStatus.RETRYABLE_FAILED.value,
}


class TelegramNotificationOutbox(Base):
    __tablename__ = "telegram_notification_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="ux_telegram_notification_outbox_dedupe_key"),
        Index("ix_telegram_notification_outbox_recipient", "recipient_user_id", "created_at"),
        Index("ix_telegram_notification_outbox_source", "source_type", "source_id"),
        Index(
            "ix_telegram_notification_outbox_active_queue",
            "next_retry_at",
            "id",
            postgresql_where=text("status IN ('pending', 'sending', 'retryable_failed')"),
        ),
        Index(
            "ix_telegram_notification_outbox_lease_recovery",
            "lease_until",
            "id",
            postgresql_where=text("status = 'sending' AND lease_until IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    dedupe_key = Column(String(192), nullable=False)
    source_type = Column(String(80), nullable=False)
    source_id = Column(String(120), nullable=True)
    recipient_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    telegram_id_at_enqueue = Column(BigInteger, nullable=True)
    telegram_id_at_send = Column(BigInteger, nullable=True)
    text = Column(Text, nullable=False)
    parse_mode = Column(String(32), nullable=True)
    status = Column(
        Enum(
            TelegramNotificationOutboxStatus,
            name="telegramnotificationoutboxstatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TelegramNotificationOutboxStatus.PENDING,
    )
    reason = Column(String(120), nullable=True)
    telegram_message_id = Column(BigInteger, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error_class = Column(String(120), nullable=True)
    last_error_message = Column(Text, nullable=True)
    worker_id = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    extra_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
