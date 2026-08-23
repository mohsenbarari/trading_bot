"""Durable B2B dispatch command for a publisher-owned Telegram job."""
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.sql import func

from core.telegram_multi_publisher_contract import (
    TELEGRAM_PUBLISHER_IDENTITIES,
    TelegramPublisherDispatchState,
)

from .database import Base


_PUBLISHER_IDENTITY_SQL = ", ".join(
    f"'{identity}'" for identity in TELEGRAM_PUBLISHER_IDENTITIES
)
_DISPATCH_STATE_SQL = ", ".join(
    f"'{state.value}'" for state in TelegramPublisherDispatchState
)


class TelegramPublisherDispatchCommand(Base):
    __tablename__ = "telegram_publisher_dispatch_commands"
    __table_args__ = (
        UniqueConstraint(
            "command_id",
            name="ux_telegram_publisher_dispatch_commands_command_id",
        ),
        UniqueConstraint(
            "job_id",
            name="ux_telegram_publisher_dispatch_commands_job_id",
        ),
        CheckConstraint(
            f"publisher_bot_identity IN ({_PUBLISHER_IDENTITY_SQL})",
            name="ck_telegram_publisher_dispatch_commands_publisher",
        ),
        CheckConstraint(
            f"state IN ({_DISPATCH_STATE_SQL})",
            name="ck_telegram_publisher_dispatch_commands_state",
        ),
        CheckConstraint(
            "dispatch_sequence > 0 AND attempt_count >= 0 AND lease_token >= 0",
            name="ck_telegram_publisher_dispatch_commands_counters",
        ),
        CheckConstraint(
            "(state = 'acknowledged') = (acknowledged_at IS NOT NULL)",
            name="ck_telegram_publisher_dispatch_commands_acknowledged_at",
        ),
        CheckConstraint(
            "receipt_sequence IS NULL OR receipt_sequence > 0",
            name="ck_telegram_publisher_dispatch_commands_receipt_sequence",
        ),
        CheckConstraint(
            "(receipt_sequence IS NULL) = (receipt_received_at IS NULL)",
            name="ck_telegram_publisher_dispatch_commands_receipt_timestamp",
        ),
        Index(
            "ix_telegram_publisher_dispatch_commands_claim",
            "id",
            postgresql_where=text(
                "state IN ('pending', 'retry_due', 'sent')"
            ),
        ),
        Index(
            "ix_telegram_publisher_dispatch_commands_lease_recovery",
            "lease_until",
            "id",
            postgresql_where=text(
                "state IN ('pending', 'sent', 'retry_due') AND lease_until IS NOT NULL"
            ),
        ),
        Index(
            "ix_telegram_publisher_dispatch_commands_lane_state",
            "publisher_bot_identity",
            "state",
            "next_retry_at",
            "id",
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    command_id = Column(String(64), nullable=False)
    job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    publisher_bot_identity = Column(String(32), nullable=False)
    dispatch_sequence = Column(BigInteger, nullable=False)
    state = Column(
        String(32),
        nullable=False,
        default=TelegramPublisherDispatchState.PENDING.value,
        server_default=text("'pending'"),
    )
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    lease_token = Column(BigInteger, nullable=False, default=0, server_default=text("0"))
    lease_until = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    receipt_sequence = Column(BigInteger, nullable=True)
    receipt_received_at = Column(DateTime(timezone=True), nullable=True)
    last_error_class = Column(String(120), nullable=True)
    last_error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
