# models/message.py
"""
مدل پیام‌ها برای سیستم چت
"""
from sqlalchemy import Column, Integer, String, BigInteger, Enum, Boolean, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base
from core.enums import MessageType


class Message(Base):
    """مدل پیام"""
    __tablename__ = "messages"
    
    id = Column(Integer, primary_key=True, index=True)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    receiver_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    
    # محتوای پیام: متن برای پیام‌های متنی، URL برای تصاویر، ID استیکر برای استیکرها
    content = Column(Text, nullable=False)
    message_type = Column(Enum(MessageType), nullable=False, default=MessageType.TEXT)
    
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
    
    # Edit/Delete flags
    is_deleted = Column(Boolean, nullable=False, default=False)
    edit_history = Column(JSON, nullable=False, default=list)
    
    # روابط
    sender = relationship("User", foreign_keys=[sender_id], backref="sent_messages")
    receiver = relationship("User", foreign_keys=[receiver_id], backref="received_messages")
