"""bind project-user notifications to the shared Telegram delivery queue

Revision ID: f8b3c4d5e6fd
Revises: f7a2b3c4d5ec
Create Date: 2026-07-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8b3c4d5e6fd"
down_revision: Union[str, Sequence[str], None] = "f7a2b3c4d5ec"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_notification_outbox",
        sa.Column("queue_job_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "telegram_notification_outbox",
        sa.Column("queue_handed_off_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_telegram_notification_outbox_queue_job",
        "telegram_notification_outbox",
        "telegram_delivery_jobs",
        ["queue_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_telegram_notification_outbox_queue_binding",
        "telegram_notification_outbox",
        "((queue_job_id IS NULL AND queue_handed_off_at IS NULL) OR "
        "(queue_job_id IS NOT NULL AND queue_handed_off_at IS NOT NULL))",
    )
    op.create_index(
        "ux_telegram_notification_outbox_queue_job",
        "telegram_notification_outbox",
        ["queue_job_id"],
        unique=True,
        postgresql_where=sa.text("queue_job_id IS NOT NULL"),
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


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_notification_outbox_queue_handoff",
        table_name="telegram_notification_outbox",
    )
    op.drop_index(
        "ux_telegram_notification_outbox_queue_job",
        table_name="telegram_notification_outbox",
    )
    op.drop_constraint(
        "ck_telegram_notification_outbox_queue_binding",
        "telegram_notification_outbox",
        type_="check",
    )
    op.drop_constraint(
        "fk_telegram_notification_outbox_queue_job",
        "telegram_notification_outbox",
        type_="foreignkey",
    )
    op.drop_column("telegram_notification_outbox", "queue_handed_off_at")
    op.drop_column("telegram_notification_outbox", "queue_job_id")
