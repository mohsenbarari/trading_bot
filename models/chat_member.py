# models/chat_member.py
"""
Membership model for generic chat rooms.
"""
from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Index, Integer, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.enums import ChatMemberRole, ChatMembershipStatus

from .database import Base


class ChatMember(Base):
    """Tracks membership and role state for a user inside a chat room."""

    __tablename__ = "chat_members"

    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id", name="fk_chat_members_chat"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", name="fk_chat_members_user"), nullable=False, index=True)
    role = Column(Enum(ChatMemberRole), nullable=False, default=ChatMemberRole.MEMBER)
    membership_status = Column(Enum(ChatMembershipStatus), nullable=False, default=ChatMembershipStatus.ACTIVE)
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    left_at = Column(DateTime(timezone=True), nullable=True)
    last_read_message_id = Column(
        Integer,
        ForeignKey("messages.id", name="fk_chat_members_last_read_message"),
        nullable=True,
    )
    last_read_at = Column(DateTime(timezone=True), nullable=True)
    is_muted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ux_chat_members_active_membership",
            chat_id,
            user_id,
            unique=True,
            postgresql_where=text("membership_status = 'ACTIVE'"),
        ),
        Index(
            "ix_chat_members_user_status_updated",
            user_id,
            membership_status,
            updated_at,
        ),
        Index(
            "ix_chat_members_chat_status_role",
            chat_id,
            membership_status,
            role,
        ),
    )

    chat = relationship("Chat", back_populates="members", foreign_keys=[chat_id])
    user = relationship("User", foreign_keys=[user_id])
    last_read_message = relationship("Message", foreign_keys=[last_read_message_id])