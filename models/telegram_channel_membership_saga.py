"""Foreign-local durable ban/unban saga for Telegram channel removal."""
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

from .database import Base


class TelegramChannelMembershipSaga(Base):
    __tablename__ = "telegram_channel_membership_sagas"
    __table_args__ = (
        UniqueConstraint(
            "source_dedupe_key",
            name="ux_telegram_channel_membership_sagas_source",
        ),
        UniqueConstraint(
            "ban_job_id",
            name="ux_telegram_channel_membership_sagas_ban_job",
        ),
        UniqueConstraint(
            "unban_job_id",
            name="ux_telegram_channel_membership_sagas_unban_job",
        ),
        CheckConstraint(
            "source_kind IN ('account_deleted', 'account_inactive')",
            name="ck_telegram_channel_membership_sagas_source_kind",
        ),
        CheckConstraint(
            "state IN ('ban_pending', 'ban_succeeded', 'complete', "
            "'blocked', 'terminal_failed', 'superseded')",
            name="ck_telegram_channel_membership_sagas_state",
        ),
        CheckConstraint(
            "telegram_id > 0 AND channel_id <> 0 AND source_version > 0",
            name="ck_telegram_channel_membership_sagas_identity",
        ),
        CheckConstraint(
            "((ban_job_id IS NULL AND unban_job_id IS NULL) OR "
            "(ban_job_id IS NOT NULL AND unban_job_id IS NOT NULL))",
            name="ck_telegram_channel_membership_sagas_job_pair",
        ),
        Index(
            "ix_telegram_channel_membership_sagas_active",
            "state",
            "updated_at",
            "id",
            postgresql_where=text(
                "state IN ('ban_pending', 'ban_succeeded', 'blocked')"
            ),
        ),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    source_dedupe_key = Column(String(192), nullable=False)
    source_outbox_id = Column(
        Integer,
        ForeignKey("telegram_notification_outbox.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_kind = Column(String(32), nullable=False)
    source_version = Column(BigInteger, nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    state = Column(String(32), nullable=False, server_default="ban_pending")
    ban_job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    unban_job_id = Column(
        BigInteger,
        ForeignKey("telegram_delivery_jobs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reason = Column(String(160), nullable=True)
    last_error_class = Column(String(120), nullable=True)
    last_error_message = Column(Text, nullable=True)
    ban_succeeded_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    terminal_at = Column(DateTime(timezone=True), nullable=True)
    payload_redacted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
