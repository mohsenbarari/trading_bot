"""add durable Telegram channel membership removal saga

Revision ID: ff41e2f3a5b6
Revises: fe30d1e2f4b5
Create Date: 2026-07-19 04:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ff41e2f3a5b6"
down_revision: Union[str, Sequence[str], None] = "fe30d1e2f4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum values remain on downgrade for forward-compatible application
    # rollback; PostgreSQL cannot safely remove used enum values in place.
    op.execute(
        "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
        "'channel_member_ban'"
    )
    op.execute(
        "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
        "'channel_member_unban'"
    )
    op.create_table(
        "telegram_channel_membership_sagas",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_dedupe_key", sa.String(length=192), nullable=False),
        sa.Column("source_outbox_id", sa.Integer(), nullable=True),
        sa.Column("source_user_id", sa.Integer(), nullable=True),
        sa.Column("source_kind", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="ban_pending", nullable=False),
        sa.Column("ban_job_id", sa.BigInteger(), nullable=True),
        sa.Column("unban_job_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=160), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("ban_succeeded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "source_kind IN ('account_deleted', 'account_inactive')",
            name="ck_telegram_channel_membership_sagas_source_kind",
        ),
        sa.CheckConstraint(
            "state IN ('ban_pending', 'ban_succeeded', 'complete', "
            "'blocked', 'terminal_failed', 'superseded')",
            name="ck_telegram_channel_membership_sagas_state",
        ),
        sa.CheckConstraint(
            "telegram_id > 0 AND channel_id <> 0 AND source_version > 0",
            name="ck_telegram_channel_membership_sagas_identity",
        ),
        sa.CheckConstraint(
            "((ban_job_id IS NULL AND unban_job_id IS NULL) OR "
            "(ban_job_id IS NOT NULL AND unban_job_id IS NOT NULL))",
            name="ck_telegram_channel_membership_sagas_job_pair",
        ),
        sa.ForeignKeyConstraint(
            ["source_outbox_id"],
            ["telegram_notification_outbox.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["ban_job_id"],
            ["telegram_delivery_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["unban_job_id"],
            ["telegram_delivery_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_dedupe_key",
            name="ux_telegram_channel_membership_sagas_source",
        ),
        sa.UniqueConstraint(
            "ban_job_id",
            name="ux_telegram_channel_membership_sagas_ban_job",
        ),
        sa.UniqueConstraint(
            "unban_job_id",
            name="ux_telegram_channel_membership_sagas_unban_job",
        ),
    )
    op.create_index(
        "ix_telegram_channel_membership_sagas_active",
        "telegram_channel_membership_sagas",
        ["state", "updated_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "state IN ('ban_pending', 'ban_succeeded', 'blocked')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_channel_membership_sagas_active",
        table_name="telegram_channel_membership_sagas",
    )
    op.drop_table("telegram_channel_membership_sagas")
