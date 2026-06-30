"""Telegram-bot-only admin broadcast delivery models."""
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
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


class TelegramAdminBroadcastAudienceType(str, enum.Enum):
    ALL = "all"
    GROUP = "group"
    SELECTED = "selected"


class TelegramAdminBroadcastStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    FAILED = "failed"


class TelegramAdminBroadcastReceiptStatus(str, enum.Enum):
    PENDING = "pending"
    SENDING = "sending"
    RETRYABLE_FAILED = "retryable_failed"
    SENT = "sent"
    SKIPPED = "skipped"
    TERMINAL_FAILED = "terminal_failed"


TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES = {
    TelegramAdminBroadcastReceiptStatus.SENT.value,
    TelegramAdminBroadcastReceiptStatus.SKIPPED.value,
    TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED.value,
}


NON_TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES = {
    TelegramAdminBroadcastReceiptStatus.PENDING.value,
    TelegramAdminBroadcastReceiptStatus.SENDING.value,
    TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED.value,
}


class TelegramAdminBroadcast(Base):
    __tablename__ = "telegram_admin_broadcasts"
    __table_args__ = (
        Index("ix_telegram_admin_broadcasts_created", "created_at", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    audience_type = Column(
        Enum(
            TelegramAdminBroadcastAudienceType,
            name="telegramadminbroadcastaudiencetype",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    target_groups = Column(JSON, nullable=False, default=list)
    recipient_count = Column(Integer, nullable=False, default=0)
    status = Column(
        Enum(
            TelegramAdminBroadcastStatus,
            name="telegramadminbroadcaststatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TelegramAdminBroadcastStatus.QUEUED,
    )
    queued_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    created_by = relationship("User", foreign_keys=[created_by_id])
    receipts = relationship("TelegramAdminBroadcastReceipt", back_populates="broadcast")


class TelegramAdminBroadcastReceipt(Base):
    __tablename__ = "telegram_admin_broadcast_receipts"
    __table_args__ = (
        UniqueConstraint(
            "broadcast_id",
            "recipient_user_id",
            name="ux_telegram_admin_broadcast_receipts_broadcast_recipient",
        ),
        UniqueConstraint("dedupe_key", name="ux_telegram_admin_broadcast_receipts_dedupe_key"),
        Index("ix_telegram_admin_broadcast_receipts_broadcast", "broadcast_id"),
        Index("ix_telegram_admin_broadcast_receipts_recipient", "recipient_user_id", "created_at"),
        Index(
            "ix_telegram_admin_broadcast_receipts_active_queue",
            "next_retry_at",
            "id",
            postgresql_where=text("status IN ('pending', 'sending', 'retryable_failed')"),
        ),
        Index(
            "ix_telegram_admin_broadcast_receipts_lease_recovery",
            "lease_until",
            "id",
            postgresql_where=text("status = 'sending' AND lease_until IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    broadcast_id = Column(Integer, ForeignKey("telegram_admin_broadcasts.id", ondelete="CASCADE"), nullable=False)
    broadcast = relationship("TelegramAdminBroadcast", back_populates="receipts")

    recipient_user_id = Column(Integer, nullable=False)
    telegram_id_at_enqueue = Column(BigInteger, nullable=True)
    telegram_id_at_send = Column(BigInteger, nullable=True)
    dedupe_key = Column(String(192), nullable=False)
    status = Column(
        Enum(
            TelegramAdminBroadcastReceiptStatus,
            name="telegramadminbroadcastreceiptstatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TelegramAdminBroadcastReceiptStatus.PENDING,
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
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
