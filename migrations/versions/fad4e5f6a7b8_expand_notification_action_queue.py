"""expand private notification actions handed to Telegram queue

Revision ID: fad4e5f6a7b8
Revises: fac3d4e5f6a7
Create Date: 2026-07-18 16:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fad4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "fac3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(
        "ix_telegram_notification_outbox_queue_handoff",
        table_name="telegram_notification_outbox",
    )
    op.create_index(
        "ix_telegram_notification_outbox_queue_handoff",
        "telegram_notification_outbox",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('pending', 'retryable_failed') "
            "AND source_type IN "
            "('project_user_joined', 'offer_repeat_response', "
            "'queue_action:account_status', "
            "'queue_action:general_announcement', "
            "'queue_action:general_immediate', "
            "'queue_action:offer_validation_response', "
            "'queue_action:targeted_admin_message', "
            "'queue_action:trade_alternative', "
            "'queue_action:trade_noncritical', "
            "'queue_action:trade_response', "
            "'queue_action:trade_unavailable') "
            "AND queue_job_id IS NULL AND worker_id IS NULL "
            "AND lease_until IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_notification_outbox_queue_handoff",
        table_name="telegram_notification_outbox",
    )
    op.create_index(
        "ix_telegram_notification_outbox_queue_handoff",
        "telegram_notification_outbox",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('pending', 'retryable_failed') "
            "AND source_type IN "
            "('project_user_joined', 'offer_repeat_response') "
            "AND queue_job_id IS NULL AND worker_id IS NULL "
            "AND lease_until IS NULL"
        ),
    )
