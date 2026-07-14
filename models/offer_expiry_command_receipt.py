"""Home-local durable receipt for forwarded offer-expiry commands."""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from .database import Base


class OfferExpiryCommandReceipt(Base):
    __tablename__ = "offer_expiry_command_receipts"
    __table_args__ = (
        CheckConstraint("length(request_hash) = 64", name="ck_offer_expiry_receipts_request_hash"),
        CheckConstraint(
            "((outcome_code IS NULL AND completed_at IS NULL) "
            "OR (outcome_code IS NOT NULL AND completed_at IS NOT NULL))",
            name="ck_offer_expiry_receipts_terminal_atomic",
        ),
        UniqueConstraint("command_id", name="ux_offer_expiry_receipts_command_id"),
        UniqueConstraint("idempotency_key", name="ux_offer_expiry_receipts_idempotency_key"),
        Index("ix_offer_expiry_receipts_completed_at", "completed_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    command_id = Column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    idempotency_key = Column(String(192), nullable=False)
    request_hash = Column(String(64), nullable=False)
    offer_public_id = Column(String(40), nullable=False, index=True)
    replacement_offer_public_id = Column(String(40), nullable=True, index=True)
    source_server = Column(String(16), nullable=False)
    source_surface = Column(String(32), nullable=False)
    expire_reason = Column(String(32), nullable=False)
    outcome_code = Column(String(64), nullable=True)
    first_received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
