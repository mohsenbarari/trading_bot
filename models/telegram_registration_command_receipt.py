"""Iran-local idempotency receipt for Telegram registration commands."""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .database import Base


class TelegramRegistrationCommandReceipt(Base):
    __tablename__ = "telegram_registration_command_receipts"
    __table_args__ = (
        CheckConstraint("length(request_hash) = 64", name="ck_telegram_registration_receipts_request_hash"),
        CheckConstraint(
            "length(invitation_token_hash) = 64",
            name="ck_telegram_registration_receipts_token_hash",
        ),
        CheckConstraint(
            "source_server = 'foreign'",
            name="ck_telegram_registration_receipts_source_foreign",
        ),
        CheckConstraint(
            "((outcome_code IS NULL AND completed_at IS NULL AND authoritative_user_id IS NULL) "
            "OR (outcome_code IS NOT NULL AND completed_at IS NOT NULL))",
            name="ck_telegram_registration_receipts_terminal_atomic",
        ),
        CheckConstraint(
            "((outcome_code IN ('created', 'linked_existing', 'already_linked') "
            "AND authoritative_user_id IS NOT NULL) "
            "OR (outcome_code IS NULL AND authoritative_user_id IS NULL) "
            "OR (outcome_code NOT IN ('created', 'linked_existing', 'already_linked') "
            "AND authoritative_user_id IS NULL))",
            name="ck_telegram_registration_receipts_user_outcome",
        ),
        UniqueConstraint("command_id", name="ux_telegram_registration_receipts_command_id"),
        UniqueConstraint("idempotency_key", name="ux_telegram_registration_receipts_idempotency_key"),
        Index("ix_telegram_registration_receipts_completed_at", "completed_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    command_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    idempotency_key = Column(String(192), nullable=False)
    request_hash = Column(String(64), nullable=False)
    outcome_code = Column(String(96), nullable=True)
    authoritative_user_id = Column(Integer, nullable=True)
    invitation_token_hash = Column(String(64), nullable=False)
    source_server = Column(String(16), nullable=False)
    first_received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
