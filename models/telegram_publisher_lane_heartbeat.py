"""Durable liveness lease for a publisher execution lane."""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    String,
    text,
)
from sqlalchemy.sql import func

from core.telegram_multi_publisher_contract import TELEGRAM_PUBLISHER_IDENTITIES

from .database import Base


_PUBLISHER_IDENTITY_SQL = ", ".join(
    f"'{identity}'" for identity in TELEGRAM_PUBLISHER_IDENTITIES
)


class TelegramPublisherLaneHeartbeat(Base):
    __tablename__ = "telegram_publisher_lane_heartbeats"
    __table_args__ = (
        CheckConstraint(
            f"publisher_bot_identity IN ({_PUBLISHER_IDENTITY_SQL})",
            name="ck_telegram_publisher_lane_heartbeats_publisher",
        ),
    )

    publisher_bot_identity = Column(String(32), primary_key=True)
    worker_id = Column(String(128), nullable=False)
    lease_until = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
