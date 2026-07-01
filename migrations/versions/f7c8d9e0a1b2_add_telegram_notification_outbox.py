"""add telegram notification outbox

Revision ID: f7c8d9e0a1b2
Revises: f6c7d8e9f0a2
Create Date: 2026-07-01 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f7c8d9e0a1b2"
down_revision: Union[str, Sequence[str], None] = "f6c7d8e9f0a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


telegram_notification_outbox_status = postgresql.ENUM(
    "pending",
    "sending",
    "retryable_failed",
    "sent",
    "skipped",
    "terminal_failed",
    name="telegramnotificationoutboxstatus",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'telegramnotificationoutboxstatus') THEN
                CREATE TYPE telegramnotificationoutboxstatus AS ENUM (
                    'pending',
                    'sending',
                    'retryable_failed',
                    'sent',
                    'skipped',
                    'terminal_failed'
                );
            END IF;
        END
        $$;
        """
    )

    op.create_table(
        "telegram_notification_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=192), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=True),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("telegram_id_at_enqueue", sa.BigInteger(), nullable=True),
        sa.Column("telegram_id_at_send", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_mode", sa.String(length=32), nullable=True),
        sa.Column("status", telegram_notification_outbox_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="ux_telegram_notification_outbox_dedupe_key"),
    )
    op.create_index(op.f("ix_telegram_notification_outbox_id"), "telegram_notification_outbox", ["id"], unique=False)
    op.create_index(
        op.f("ix_telegram_notification_outbox_recipient_user_id"),
        "telegram_notification_outbox",
        ["recipient_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_notification_outbox_recipient",
        "telegram_notification_outbox",
        ["recipient_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_notification_outbox_source",
        "telegram_notification_outbox",
        ["source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_notification_outbox_active_queue",
        "telegram_notification_outbox",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'sending', 'retryable_failed')"),
    )
    op.create_index(
        "ix_telegram_notification_outbox_lease_recovery",
        "telegram_notification_outbox",
        ["lease_until", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'sending' AND lease_until IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_notification_outbox_lease_recovery", table_name="telegram_notification_outbox")
    op.drop_index("ix_telegram_notification_outbox_active_queue", table_name="telegram_notification_outbox")
    op.drop_index("ix_telegram_notification_outbox_source", table_name="telegram_notification_outbox")
    op.drop_index("ix_telegram_notification_outbox_recipient", table_name="telegram_notification_outbox")
    op.drop_index(op.f("ix_telegram_notification_outbox_recipient_user_id"), table_name="telegram_notification_outbox")
    op.drop_index(op.f("ix_telegram_notification_outbox_id"), table_name="telegram_notification_outbox")
    op.drop_table("telegram_notification_outbox")
    telegram_notification_outbox_status.drop(op.get_bind(), checkfirst=True)
