"""add telegram admin broadcasts

Revision ID: f6c7d8e9f0a2
Revises: f5c6d7e8f9a1
Create Date: 2026-06-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f6c7d8e9f0a2"
down_revision: Union[str, Sequence[str], None] = "f5c6d7e8f9a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


telegram_admin_broadcast_audience_type = postgresql.ENUM(
    "all",
    "group",
    "selected",
    name="telegramadminbroadcastaudiencetype",
    create_type=False,
)
telegram_admin_broadcast_status = postgresql.ENUM(
    "queued",
    "running",
    "completed",
    "completed_with_errors",
    "failed",
    name="telegramadminbroadcaststatus",
    create_type=False,
)
telegram_admin_broadcast_receipt_status = postgresql.ENUM(
    "pending",
    "sending",
    "retryable_failed",
    "sent",
    "skipped",
    "terminal_failed",
    name="telegramadminbroadcastreceiptstatus",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'telegramadminbroadcastaudiencetype') THEN
                CREATE TYPE telegramadminbroadcastaudiencetype AS ENUM ('all', 'group', 'selected');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'telegramadminbroadcaststatus') THEN
                CREATE TYPE telegramadminbroadcaststatus AS ENUM (
                    'queued',
                    'running',
                    'completed',
                    'completed_with_errors',
                    'failed'
                );
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'telegramadminbroadcastreceiptstatus') THEN
                CREATE TYPE telegramadminbroadcastreceiptstatus AS ENUM (
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
        "telegram_admin_broadcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("audience_type", telegram_admin_broadcast_audience_type, nullable=False),
        sa.Column("target_groups", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status", telegram_admin_broadcast_status, nullable=False, server_default=sa.text("'queued'")),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_telegram_admin_broadcasts_id"), "telegram_admin_broadcasts", ["id"], unique=False)
    op.create_index(
        op.f("ix_telegram_admin_broadcasts_created_by_id"),
        "telegram_admin_broadcasts",
        ["created_by_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_admin_broadcasts_created",
        "telegram_admin_broadcasts",
        ["created_at", "id"],
        unique=False,
    )

    op.create_table(
        "telegram_admin_broadcast_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("broadcast_id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_id_at_enqueue", sa.BigInteger(), nullable=True),
        sa.Column("telegram_id_at_send", sa.BigInteger(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=192), nullable=False),
        sa.Column("status", telegram_admin_broadcast_receipt_status, nullable=False, server_default=sa.text("'pending'")),
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
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["broadcast_id"], ["telegram_admin_broadcasts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "broadcast_id",
            "recipient_user_id",
            name="ux_telegram_admin_broadcast_receipts_broadcast_recipient",
        ),
        sa.UniqueConstraint("dedupe_key", name="ux_telegram_admin_broadcast_receipts_dedupe_key"),
    )
    op.create_index(
        op.f("ix_telegram_admin_broadcast_receipts_id"),
        "telegram_admin_broadcast_receipts",
        ["id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_admin_broadcast_receipts_broadcast",
        "telegram_admin_broadcast_receipts",
        ["broadcast_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_admin_broadcast_receipts_recipient",
        "telegram_admin_broadcast_receipts",
        ["recipient_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_admin_broadcast_receipts_active_queue",
        "telegram_admin_broadcast_receipts",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'sending', 'retryable_failed')"),
    )
    op.create_index(
        "ix_telegram_admin_broadcast_receipts_lease_recovery",
        "telegram_admin_broadcast_receipts",
        ["lease_until", "id"],
        unique=False,
        postgresql_where=sa.text("status = 'sending' AND lease_until IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_admin_broadcast_receipts_lease_recovery", table_name="telegram_admin_broadcast_receipts")
    op.drop_index("ix_telegram_admin_broadcast_receipts_active_queue", table_name="telegram_admin_broadcast_receipts")
    op.drop_index("ix_telegram_admin_broadcast_receipts_recipient", table_name="telegram_admin_broadcast_receipts")
    op.drop_index("ix_telegram_admin_broadcast_receipts_broadcast", table_name="telegram_admin_broadcast_receipts")
    op.drop_index(op.f("ix_telegram_admin_broadcast_receipts_id"), table_name="telegram_admin_broadcast_receipts")
    op.drop_table("telegram_admin_broadcast_receipts")

    op.drop_index("ix_telegram_admin_broadcasts_created", table_name="telegram_admin_broadcasts")
    op.drop_index(op.f("ix_telegram_admin_broadcasts_created_by_id"), table_name="telegram_admin_broadcasts")
    op.drop_index(op.f("ix_telegram_admin_broadcasts_id"), table_name="telegram_admin_broadcasts")
    op.drop_table("telegram_admin_broadcasts")

    op.execute("DROP TYPE IF EXISTS telegramadminbroadcastreceiptstatus")
    op.execute("DROP TYPE IF EXISTS telegramadminbroadcaststatus")
    op.execute("DROP TYPE IF EXISTS telegramadminbroadcastaudiencetype")
