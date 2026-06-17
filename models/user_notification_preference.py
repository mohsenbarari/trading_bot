"""Per-user notification preferences."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from .database import Base


class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"

    __table_args__ = (
        Index("ix_user_notification_preferences_user_id", "user_id", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    market_offer_push_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", backref=backref("notification_preference", uselist=False))
