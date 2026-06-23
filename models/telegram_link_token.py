"""Short-lived WebApp-issued Telegram account-link tokens."""

import enum

from sqlalchemy import BigInteger, Column, DateTime, Enum, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


def _status_values(enum_cls):
    return [status.value for status in enum_cls]


class TelegramLinkTokenStatus(str, enum.Enum):
    PENDING = "pending"
    USED = "used"
    REVOKED = "revoked"
    EXPIRED = "expired"


class TelegramLinkToken(Base):
    __tablename__ = "telegram_link_tokens"
    __table_args__ = (
        Index("ix_telegram_link_tokens_user_status", "user_id", "status"),
        Index("ix_telegram_link_tokens_expires_at", "expires_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    status = Column(
        Enum(
            TelegramLinkTokenStatus,
            name="telegramlinktokenstatus",
            values_callable=_status_values,
        ),
        nullable=False,
        default=TelegramLinkTokenStatus.PENDING,
        index=True,
    )
    issued_by_server = Column(String(16), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    used_telegram_id = Column(BigInteger, nullable=True, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User")
