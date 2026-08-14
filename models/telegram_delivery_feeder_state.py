"""Foreign-local durable state for subordinate Telegram queue feeders."""
from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, JSON, String, text
from sqlalchemy.sql import func

from .database import Base


class TelegramDeliveryFeederState(Base):
    __tablename__ = "telegram_delivery_feeder_states"
    __table_args__ = (
        CheckConstraint(
            "feeder_kind IN ('offer_edit')",
            name="ck_telegram_delivery_feeder_states_kind",
        ),
    )

    feeder_kind = Column(String(32), primary_key=True)
    fresh_success_counts = Column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'::json"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
