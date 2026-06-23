# trading_bot/models/notification.py
"""مدل نوتیفیکیشن"""

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Enum, Index, JSON, Text, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
from core.enums import NotificationLevel, NotificationCategory


class Notification(Base):
    __tablename__ = "notifications"
    
    # ===== Database Indexes =====
    __table_args__ = (
        # ایندکس ترکیبی برای کوئری "نوتیفیکیشن‌های خوانده نشده کاربر"
        Index('ix_notifications_user_unread', 'user_id', 'is_read'),
        Index(
            "ux_notifications_dedupe_key_not_null",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    level = Column(Enum(NotificationLevel), default=NotificationLevel.INFO, nullable=False)
    category = Column(Enum(NotificationCategory), default=NotificationCategory.SYSTEM, nullable=False)
    dedupe_key = Column(String(180), nullable=True)
    extra_payload = Column(JSON, nullable=True)
    
    # رابطه با کاربر
    user = relationship("User", backref="notifications")
