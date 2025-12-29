# models/conversation.py
"""
مدل مکالمه‌ها برای سیستم چت
"""
from sqlalchemy import Column, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class Conversation(Base):
    """
    مدل مکالمه - هر مکالمه بین دو کاربر یکتاست
    برای بهینه‌سازی و نمایش لیست چت‌ها استفاده می‌شود
    """
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # دو طرف مکالمه - user1_id همیشه کوچکتر از user2_id است (برای یکتایی)
    user1_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    user2_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # آخرین پیام برای نمایش پیش‌نمایش
    last_message_id = Column(Integer, ForeignKey("messages.id"), nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # تعداد پیام‌های خوانده نشده برای هر کاربر
    unread_count_user1 = Column(Integer, nullable=False, default=0)
    unread_count_user2 = Column(Integer, nullable=False, default=0)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    
    # روابط
    user1 = relationship("User", foreign_keys=[user1_id])
    user2 = relationship("User", foreign_keys=[user2_id])
    last_message = relationship("Message", foreign_keys=[last_message_id])
    
    # یکتایی: هر جفت کاربر فقط یک مکالمه دارند
    __table_args__ = (
        UniqueConstraint('user1_id', 'user2_id', name='uq_conversation_users'),
    )
