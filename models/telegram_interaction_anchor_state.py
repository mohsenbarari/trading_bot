"""Foreign-local durable reply-keyboard anchor state for Telegram Bot UX."""
from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.sql import func

from .database import Base


class TelegramInteractionAnchorState(Base):
    """Fence asynchronous send results by chat-local anchor generation."""

    __tablename__ = "telegram_interaction_anchor_states"
    __table_args__ = (
        CheckConstraint(
            "chat_id <> 0",
            name="ck_telegram_interaction_anchor_states_chat_id",
        ),
        CheckConstraint(
            "desired_generation > 0",
            name="ck_telegram_interaction_anchor_states_desired_generation",
        ),
        CheckConstraint(
            "active_generation IS NULL OR active_generation > 0",
            name="ck_telegram_interaction_anchor_states_active_generation",
        ),
        CheckConstraint(
            "active_message_id IS NULL OR active_message_id > 0",
            name="ck_telegram_interaction_anchor_states_active_message_id",
        ),
        CheckConstraint(
            "active_generation IS NULL OR active_generation <= desired_generation",
            name="ck_telegram_interaction_anchor_states_generation_order",
        ),
        CheckConstraint(
            "((active_generation IS NULL AND active_message_id IS NULL AND "
            "active_logical_message_key IS NULL) OR "
            "(active_generation IS NOT NULL AND active_message_id IS NOT NULL "
            "AND active_logical_message_key IS NOT NULL))",
            name="ck_telegram_interaction_anchor_states_active_tuple",
        ),
        CheckConstraint(
            "active_generation IS NOT NULL OR active_outbox_id IS NULL",
            name="ck_telegram_interaction_anchor_active_outbox",
        ),
        Index(
            "ix_telegram_interaction_anchor_states_recipient",
            "recipient_user_id",
        ),
        Index(
            "ux_telegram_interaction_anchor_states_desired_outbox",
            "desired_outbox_id",
            unique=True,
            postgresql_where=text("desired_outbox_id IS NOT NULL"),
        ),
        Index(
            "ux_telegram_interaction_anchor_states_active_outbox",
            "active_outbox_id",
            unique=True,
            postgresql_where=text("active_outbox_id IS NOT NULL"),
        ),
    )

    chat_id = Column(BigInteger, primary_key=True, autoincrement=False)
    recipient_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    desired_generation = Column(BigInteger, nullable=False)
    desired_outbox_id = Column(
        Integer,
        ForeignKey("telegram_notification_outbox.id", ondelete="SET NULL"),
        nullable=True,
    )
    desired_logical_message_key = Column(String(192), nullable=False)
    active_generation = Column(BigInteger, nullable=True)
    active_outbox_id = Column(
        Integer,
        ForeignKey("telegram_notification_outbox.id", ondelete="SET NULL"),
        nullable=True,
    )
    active_message_id = Column(BigInteger, nullable=True)
    active_logical_message_key = Column(String(192), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
