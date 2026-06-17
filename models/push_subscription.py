"""Web Push subscription model."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    __table_args__ = (
        Index("ix_push_subscriptions_user_enabled", "user_id", "enabled"),
        Index("ix_push_subscriptions_endpoint_hash", "endpoint_hash", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    endpoint = Column(Text, nullable=False)
    endpoint_hash = Column(String(64), nullable=False)
    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)
    user_agent = Column(Text, nullable=True)
    platform = Column(String(80), nullable=True)
    enabled = Column(Boolean, default=True, nullable=False)
    failure_count = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", backref="push_subscriptions")
