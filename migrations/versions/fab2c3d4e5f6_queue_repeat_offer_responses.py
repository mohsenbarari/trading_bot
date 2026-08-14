"""queue repeat-offer bot responses

Revision ID: fab2c3d4e5f6
Revises: faa1b2c3d4e5
Create Date: 2026-07-18 09:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fab2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "faa1b2c3d4e5"
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
            "('project_user_joined', 'offer_repeat_response') "
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
            "AND source_type = 'project_user_joined' "
            "AND queue_job_id IS NULL AND worker_id IS NULL "
            "AND lease_until IS NULL"
        ),
    )
