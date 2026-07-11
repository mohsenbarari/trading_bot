"""Foreign-local durable state for Telegram-first registration collection."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .database import Base


def _enum_values(enum_cls):
    return [item.value for item in enum_cls]


class TelegramRegistrationIntentStatus(str, enum.Enum):
    COLLECTING = "collecting"
    READY = "ready"
    FORWARDING = "forwarding"
    RETRY_WAIT = "retry_wait"
    RECONCILED_CREATED = "reconciled_created"
    RECONCILED_LINKED_EXISTING = "reconciled_linked_existing"
    RECONCILED_ALREADY_LINKED = "reconciled_already_linked"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TelegramRegistrationIntent(Base):
    __tablename__ = "telegram_registration_intents"
    __table_args__ = (
        CheckConstraint("telegram_id > 0", name="ck_telegram_registration_intents_telegram_id_positive"),
        CheckConstraint("retry_count >= 0", name="ck_telegram_registration_intents_retry_count_nonnegative"),
        UniqueConstraint("idempotency_key", name="ux_telegram_registration_intents_idempotency_key"),
        Index("ix_telegram_registration_intents_due", "status", "next_retry_at", "created_at"),
        Index("ix_telegram_registration_intents_telegram_id", "telegram_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key = Column(String(192), nullable=False)
    invitation_token = Column(String(192), nullable=False)
    normalized_mobile = Column(String(32), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    telegram_username = Column(String(255), nullable=True)
    telegram_full_name = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    contact_verified_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    invitation_expires_at_snapshot = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        Enum(
            TelegramRegistrationIntentStatus,
            name="telegramregistrationintentstatus",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TelegramRegistrationIntentStatus.COLLECTING,
        server_default=text("'collecting'"),
    )
    retry_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    last_error_code = Column(String(96), nullable=True)
    authoritative_user_id = Column(Integer, nullable=True)
    projected_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
