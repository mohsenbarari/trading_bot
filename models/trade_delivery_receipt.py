"""Durable delivery receipts for trade-completion notifications."""
from __future__ import annotations

import enum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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


class TradeDeliveryChannel(str, enum.Enum):
    WEBAPP = "webapp"
    TELEGRAM = "telegram"


class TradeDeliveryReceiptStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_PENDING = "retry_pending"
    SENT = "sent"
    SKIPPED = "skipped"
    NOT_REQUIRED = "not_required"
    PERMANENT_FAILED = "permanent_failed"


TERMINAL_TRADE_DELIVERY_RECEIPT_STATUSES = {
    TradeDeliveryReceiptStatus.SENT.value,
    TradeDeliveryReceiptStatus.SKIPPED.value,
    TradeDeliveryReceiptStatus.NOT_REQUIRED.value,
    TradeDeliveryReceiptStatus.PERMANENT_FAILED.value,
}


class TradeDeliveryReceipt(Base):
    __tablename__ = "trade_delivery_receipts"
    __table_args__ = (
        CheckConstraint(
            "destination_server IN ('iran', 'foreign')",
            name="ck_trade_delivery_receipts_destination_server",
        ),
        UniqueConstraint(
            "event_type",
            "trade_number",
            "recipient_user_id",
            "channel",
            name="ux_trade_delivery_receipts_event_trade_recipient_channel",
        ),
        UniqueConstraint("dedupe_key", name="ux_trade_delivery_receipts_dedupe_key"),
        Index("ix_trade_delivery_receipts_trade_id", "trade_id"),
        Index("ix_trade_delivery_receipts_offer_id", "offer_id"),
        Index("ix_trade_delivery_receipts_notification_id", "notification_id"),
        Index("ix_trade_delivery_receipts_recipient", "recipient_user_id", "event_created_at"),
        Index("ix_trade_delivery_receipts_trade_audit", "event_type", "trade_number"),
        Index(
            "ix_trade_delivery_receipts_queue",
            "destination_server",
            "channel",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_trade_delivery_receipts_active_state",
            "destination_server",
            "status",
            "next_retry_at",
            postgresql_where=text("status IN ('pending', 'processing', 'retry_pending')"),
        ),
        Index(
            "ix_trade_delivery_receipts_lease_recovery",
            "destination_server",
            "lease_until",
            postgresql_where=text("status = 'processing' AND lease_until IS NOT NULL"),
        ),
        Index(
            "ix_trade_delivery_receipts_terminal_cleanup",
            "terminal_at",
            "status",
            postgresql_where=text("terminal_at IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(64), nullable=False)
    dedupe_key = Column(String(192), nullable=False)

    trade_id = Column(Integer, ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)
    trade = relationship("Trade", foreign_keys=[trade_id])
    trade_number = Column(Integer, nullable=False)
    offer_id = Column(Integer, ForeignKey("offers.id", ondelete="SET NULL"), nullable=True)
    offer = relationship("Offer", foreign_keys=[offer_id])

    recipient_user_id = Column(Integer, nullable=False)
    recipient_role = Column(String(32), nullable=False)
    channel = Column(
        Enum(
            TradeDeliveryChannel,
            name="tradedeliverychannel",
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    destination_server = Column(String(16), nullable=False)
    status = Column(
        Enum(
            TradeDeliveryReceiptStatus,
            name="tradedeliveryreceiptstatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TradeDeliveryReceiptStatus.PENDING,
    )
    reason = Column(String(96), nullable=True)

    notification_id = Column(Integer, ForeignKey("notifications.id", ondelete="SET NULL"), nullable=True)
    notification = relationship("Notification", foreign_keys=[notification_id])
    telegram_message_id = Column(BigInteger, nullable=True)

    worker_id = Column(String(128), nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    last_error_class = Column(String(120), nullable=True)
    audit_payload = Column(JSON, nullable=True)

    event_created_at = Column(DateTime(timezone=True), nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
