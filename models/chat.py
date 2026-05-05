# models/chat.py
"""
Generic chat room model for direct chats, groups, and channels.
"""
from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.enums import ChatType

from .database import Base


class Chat(Base):
    """Generic room model used as the future home for all messenger surfaces."""

    __tablename__ = "chats"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum(ChatType), nullable=False, index=True)
    title = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    is_system = Column(Boolean, nullable=False, default=False)
    is_mandatory = Column(Boolean, nullable=False, default=False)
    is_deleted = Column(Boolean, nullable=False, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    max_members = Column(Integer, nullable=True)
    last_message_id = Column(Integer, ForeignKey("messages.id", name="fk_chats_last_message"), nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    created_by = relationship("User", foreign_keys=[created_by_id])
    last_message = relationship("Message", foreign_keys=[last_message_id])
    members = relationship("ChatMember", back_populates="chat")
    messages = relationship("Message", back_populates="chat", foreign_keys="Message.chat_id")