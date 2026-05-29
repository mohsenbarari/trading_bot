"""Admin-authored management message history models."""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class AdminMarketMessage(Base):
    """Pinned market management message with immutable publish history."""

    __tablename__ = "admin_market_messages"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reused_from_id = Column(Integer, ForeignKey("admin_market_messages.id"), nullable=True, index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    notified_recipients_count = Column(Integer, nullable=False, default=0)
    published_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])
    reused_from = relationship("AdminMarketMessage", remote_side=[id])


class AdminBroadcastMessage(Base):
    """One Super Admin send-to-all broadcast audit/history row."""

    __tablename__ = "admin_broadcast_messages"

    id = Column(Integer, primary_key=True, index=True)
    content = Column(Text, nullable=False)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_groups = Column(JSON, nullable=False, default=list)
    recipient_count = Column(Integer, nullable=False, default=0)
    published_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_id])