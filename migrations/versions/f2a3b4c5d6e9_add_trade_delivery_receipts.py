"""add trade delivery receipts

Revision ID: f2a3b4c5d6e9
Revises: f1a2b3c4d5e8
Create Date: 2026-06-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f2a3b4c5d6e9"
down_revision = "f1a2b3c4d5e8"
branch_labels = None
depends_on = None


trade_delivery_channel = postgresql.ENUM(
    "webapp",
    "telegram",
    name="tradedeliverychannel",
    create_type=False,
)
trade_delivery_receipt_status = postgresql.ENUM(
    "pending",
    "processing",
    "retry_pending",
    "sent",
    "skipped",
    "not_required",
    "permanent_failed",
    name="tradedeliveryreceiptstatus",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tradedeliverychannel') THEN
                CREATE TYPE tradedeliverychannel AS ENUM ('webapp', 'telegram');
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'tradedeliveryreceiptstatus') THEN
                CREATE TYPE tradedeliveryreceiptstatus AS ENUM (
                    'pending',
                    'processing',
                    'retry_pending',
                    'sent',
                    'skipped',
                    'not_required',
                    'permanent_failed'
                );
            END IF;
        END
        $$;
        """
    )
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(length=180), nullable=True))
    op.add_column("notifications", sa.Column("extra_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index(
        "ux_notifications_dedupe_key_not_null",
        "notifications",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.create_index(
        "ix_notifications_extra_payload_gin",
        "notifications",
        ["extra_payload"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("extra_payload IS NOT NULL"),
    )

    op.create_table(
        "trade_delivery_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("dedupe_key", sa.String(length=192), nullable=False),
        sa.Column("trade_id", sa.Integer(), nullable=True),
        sa.Column("trade_number", sa.Integer(), nullable=False),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("recipient_role", sa.String(length=32), nullable=False),
        sa.Column("channel", trade_delivery_channel, nullable=False),
        sa.Column("destination_server", sa.String(length=16), nullable=False),
        sa.Column("status", trade_delivery_receipt_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("reason", sa.String(length=96), nullable=True),
        sa.Column("notification_id", sa.Integer(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("audit_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("event_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "destination_server IN ('iran', 'foreign')",
            name="ck_trade_delivery_receipts_destination_server",
        ),
        sa.ForeignKeyConstraint(["notification_id"], ["notifications.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["trade_id"], ["trades.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_type",
            "trade_number",
            "recipient_user_id",
            "channel",
            name="ux_trade_delivery_receipts_event_trade_recipient_channel",
        ),
        sa.UniqueConstraint("dedupe_key", name="ux_trade_delivery_receipts_dedupe_key"),
    )
    op.create_index(op.f("ix_trade_delivery_receipts_id"), "trade_delivery_receipts", ["id"], unique=False)
    op.create_index("ix_trade_delivery_receipts_trade_id", "trade_delivery_receipts", ["trade_id"], unique=False)
    op.create_index("ix_trade_delivery_receipts_offer_id", "trade_delivery_receipts", ["offer_id"], unique=False)
    op.create_index(
        "ix_trade_delivery_receipts_notification_id",
        "trade_delivery_receipts",
        ["notification_id"],
        unique=False,
    )
    op.create_index(
        "ix_trade_delivery_receipts_recipient",
        "trade_delivery_receipts",
        ["recipient_user_id", "event_created_at"],
        unique=False,
    )
    op.create_index(
        "ix_trade_delivery_receipts_trade_audit",
        "trade_delivery_receipts",
        ["event_type", "trade_number"],
        unique=False,
    )
    op.create_index(
        "ix_trade_delivery_receipts_queue",
        "trade_delivery_receipts",
        ["destination_server", "channel", "status", "next_retry_at"],
        unique=False,
    )
    op.create_index(
        "ix_trade_delivery_receipts_active_state",
        "trade_delivery_receipts",
        ["destination_server", "status", "next_retry_at"],
        unique=False,
        postgresql_where=sa.text("status IN ('pending', 'processing', 'retry_pending')"),
    )
    op.create_index(
        "ix_trade_delivery_receipts_lease_recovery",
        "trade_delivery_receipts",
        ["destination_server", "lease_until"],
        unique=False,
        postgresql_where=sa.text("status = 'processing' AND lease_until IS NOT NULL"),
    )
    op.create_index(
        "ix_trade_delivery_receipts_terminal_cleanup",
        "trade_delivery_receipts",
        ["terminal_at", "status"],
        unique=False,
        postgresql_where=sa.text("terminal_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_trade_delivery_receipts_terminal_cleanup", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_lease_recovery", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_active_state", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_queue", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_trade_audit", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_recipient", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_notification_id", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_offer_id", table_name="trade_delivery_receipts")
    op.drop_index("ix_trade_delivery_receipts_trade_id", table_name="trade_delivery_receipts")
    op.drop_index(op.f("ix_trade_delivery_receipts_id"), table_name="trade_delivery_receipts")
    op.drop_table("trade_delivery_receipts")
    op.execute("DROP TYPE IF EXISTS tradedeliveryreceiptstatus")
    op.execute("DROP TYPE IF EXISTS tradedeliverychannel")

    op.drop_index("ix_notifications_extra_payload_gin", table_name="notifications")
    op.drop_index("ux_notifications_dedupe_key_not_null", table_name="notifications")
    op.drop_column("notifications", "extra_payload")
    op.drop_column("notifications", "dedupe_key")
