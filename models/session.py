# models/session.py
"""مدل نشست کاربر و درخواست لاگین"""
import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey,
    DateTime, Enum as SAEnum, Text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base


class Platform(str, enum.Enum):
    TELEGRAM_MINI_APP = "telegram_mini_app"
    WEB = "web"
    ANDROID = "android"


class LoginRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_name = Column(String(255), nullable=False, default="Unknown Device")
    device_ip = Column(String(45), nullable=True)
    platform = Column(SAEnum(Platform), nullable=False, default=Platform.WEB)
    refresh_token_hash = Column(String(255), nullable=True, index=True)
    is_primary = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_active_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    user = relationship("User", backref="sessions")


class SessionLoginRequest(Base):
    __tablename__ = "session_login_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    requester_device_name = Column(String(255), nullable=False, default="Unknown Device")
    requester_ip = Column(String(45), nullable=True)
    status = Column(
        SAEnum(LoginRequestStatus),
        nullable=False,
        default=LoginRequestStatus.PENDING,
        index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    resolved_by_session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("user_sessions.id", ondelete="SET NULL"),
        nullable=True
    )

    # Relationships
    user = relationship("User", backref="login_requests")
    resolved_by_session = relationship("UserSession")
