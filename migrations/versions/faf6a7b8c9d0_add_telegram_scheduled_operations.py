"""add bounded Telegram scheduled-operation sources

Revision ID: faf6a7b8c9d0
Revises: fae5f6a7b8c9
Create Date: 2026-07-18 22:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "faf6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "fae5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_BASE_QUEUE_SOURCES = (
    "'project_user_joined', 'offer_repeat_response', 'offer_success_preview', "
    "'queue_action:account_status', "
    "'queue_action:general_announcement', "
    "'queue_action:general_immediate', "
    "'queue_action:offer_validation_response', "
    "'queue_action:targeted_admin_message', "
    "'queue_action:trade_alternative', "
    "'queue_action:trade_noncritical', "
    "'queue_action:trade_response', "
    "'queue_action:trade_unavailable'"
)


def _notification_handoff_predicate(*, timed: bool) -> sa.TextClause:
    sources = _BASE_QUEUE_SOURCES
    if timed:
        sources += (
            ", 'queue_action:delayed_restriction', "
            "'queue_action:timed_security'"
        )
    return sa.text(
        "status IN ('pending', 'retryable_failed') "
        f"AND source_type IN ({sources}) "
        "AND queue_job_id IS NULL AND worker_id IS NULL "
        "AND lease_until IS NULL"
    )


def _replace_notification_index(*, timed: bool) -> None:
    op.drop_index(
        "ix_telegram_notification_outbox_queue_handoff",
        table_name="telegram_notification_outbox",
    )
    op.create_index(
        "ix_telegram_notification_outbox_queue_handoff",
        "telegram_notification_outbox",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=_notification_handoff_predicate(timed=timed),
    )


def upgrade() -> None:
    op.create_table(
        "telegram_scheduled_operations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("dedupe_key", sa.String(length=192), nullable=False),
        sa.Column("action_kind", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=120), nullable=False),
        sa.Column("source_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("destination_class", sa.String(length=16), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freshness_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_id", sa.String(length=192), nullable=True),
        sa.Column("scope_allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("queue_job_id", sa.BigInteger(), nullable=True),
        sa.Column("queue_handed_off_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reconciliation_required_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.String(length=160), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action_kind IN ('noncritical_market', 'temporary_cleanup', 'cosmetic_cleanup')",
            name="ck_telegram_scheduled_operations_action",
        ),
        sa.CheckConstraint(
            "method IN ('sendMessage', 'deleteMessage', 'editMessageReplyMarkup')",
            name="ck_telegram_scheduled_operations_method",
        ),
        sa.CheckConstraint(
            "destination_class IN ('private', 'channel')",
            name="ck_telegram_scheduled_operations_destination_class",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'skipped', 'terminal_failed', 'cancelled')",
            name="ck_telegram_scheduled_operations_status",
        ),
        sa.CheckConstraint("source_version > 0", name="ck_telegram_scheduled_operations_source_version"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_telegram_scheduled_operations_attempt_count"),
        sa.CheckConstraint(
            "((queue_job_id IS NULL AND queue_handed_off_at IS NULL) OR "
            "(queue_job_id IS NOT NULL AND queue_handed_off_at IS NOT NULL))",
            name="ck_telegram_scheduled_operations_queue_binding",
        ),
        sa.CheckConstraint(
            "NOT (queue_job_id IS NOT NULL AND reconciliation_required_at IS NOT NULL)",
            name="ck_telegram_scheduled_operations_queue_owner",
        ),
        sa.ForeignKeyConstraint(["queue_job_id"], ["telegram_delivery_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="ux_telegram_scheduled_operations_dedupe_key"),
    )
    op.create_index(
        "ix_telegram_scheduled_operations_due",
        "telegram_scheduled_operations",
        ["due_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status = 'pending' AND queue_job_id IS NULL "
            "AND reconciliation_required_at IS NULL"
        ),
    )
    op.create_index(
        "ix_telegram_scheduled_operations_recipient_user_id",
        "telegram_scheduled_operations",
        ["recipient_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_scheduled_operations_run",
        "telegram_scheduled_operations",
        ["run_id", "status"],
        unique=False,
    )
    op.create_index(
        "ux_telegram_scheduled_operations_queue_job",
        "telegram_scheduled_operations",
        ["queue_job_id"],
        unique=True,
        postgresql_where=sa.text("queue_job_id IS NOT NULL"),
    )
    _replace_notification_index(timed=True)


def downgrade() -> None:
    _replace_notification_index(timed=False)
    op.drop_index(
        "ux_telegram_scheduled_operations_queue_job",
        table_name="telegram_scheduled_operations",
    )
    op.drop_index(
        "ix_telegram_scheduled_operations_run",
        table_name="telegram_scheduled_operations",
    )
    op.drop_index(
        "ix_telegram_scheduled_operations_recipient_user_id",
        table_name="telegram_scheduled_operations",
    )
    op.drop_index(
        "ix_telegram_scheduled_operations_due",
        table_name="telegram_scheduled_operations",
    )
    op.drop_table("telegram_scheduled_operations")
