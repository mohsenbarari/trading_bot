# models/session.py
"""مدل نشست کاربر و درخواست لاگین"""
import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey,
    DateTime, Enum as SAEnum, Text, UniqueConstraint
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


class SingleSessionRecoveryStatus(str, enum.Enum):
    PENDING_ADMIN_REVIEW = "pending_admin_review"
    IDENTITY_VERIFICATION_REQUESTED = "identity_verification_requested"
    IDENTITY_SUBMITTED = "identity_submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class UserSession(Base):
    __tablename__ = "user_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    device_name = Column(String(255), nullable=False, default="Unknown Device")
    device_ip = Column(String(45), nullable=True)
    home_server = Column(String(16), nullable=False, default="foreign")
    platform = Column(SAEnum(Platform, values_callable=lambda obj: [e.value for e in obj], name="platform"), nullable=False, default=Platform.WEB)
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
    requester_home_server = Column(String(16), nullable=False, default="foreign")
    status = Column(
        SAEnum(LoginRequestStatus, values_callable=lambda obj: [e.value for e in obj], name="loginrequeststatus"),
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


class SingleSessionRecoveryRequest(Base):
    __tablename__ = "single_session_recovery_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_login_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("session_login_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requester_device_name = Column(String(255), nullable=False, default="Unknown Device")
    requester_ip = Column(String(45), nullable=True)
    status = Column(
        SAEnum(
            SingleSessionRecoveryStatus,
            values_callable=lambda obj: [e.value for e in obj],
            name="singlesessionrecoverystatus",
        ),
        nullable=False,
        default=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    inline_action_expires_at = Column(DateTime(timezone=True), nullable=False)
    chat_action_expires_at = Column(DateTime(timezone=True), nullable=False)
    identity_requested_at = Column(DateTime(timezone=True), nullable=True)
    identity_submitted_at = Column(DateTime(timezone=True), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decided_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id], backref="single_session_recovery_requests")
    session_login_request = relationship("SessionLoginRequest", backref="single_session_recovery_requests")
    decided_by_user = relationship("User", foreign_keys=[decided_by_user_id])
    admin_targets = relationship(
        "SingleSessionRecoveryAdminTarget",
        back_populates="recovery_request",
        cascade="all, delete-orphan",
    )


class SingleSessionRecoveryAdminTarget(Base):
    __tablename__ = "single_session_recovery_admin_targets"
    __table_args__ = (
        UniqueConstraint(
            "recovery_request_id",
            "admin_user_id",
            name="uq_single_session_recovery_admin_targets_recovery_admin",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recovery_request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("single_session_recovery_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    admin_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    current_action_message_id = Column(
        Integer,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    recovery_request = relationship("SingleSessionRecoveryRequest", back_populates="admin_targets")
    admin_user = relationship("User", foreign_keys=[admin_user_id])
    current_action_message = relationship("Message", foreign_keys=[current_action_message_id])
