"""bind Telegram admin broadcast receipts to the shared delivery queue

Revision ID: f7a2b3c4d5ec
Revises: f6f1c2d3e4fb
Create Date: 2026-07-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f7a2b3c4d5ec"
down_revision: Union[str, Sequence[str], None] = "f6f1c2d3e4fb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column(
            "queue_last_handed_off_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_telegram_admin_broadcasts_queue_fairness",
        "telegram_admin_broadcasts",
        ["queue_last_handed_off_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.add_column(
        "telegram_admin_broadcast_receipts",
        sa.Column("queue_job_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "telegram_admin_broadcast_receipts",
        sa.Column("queue_handed_off_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_telegram_admin_broadcast_receipts_queue_job",
        "telegram_admin_broadcast_receipts",
        "telegram_delivery_jobs",
        ["queue_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_telegram_admin_broadcast_receipts_queue_binding",
        "telegram_admin_broadcast_receipts",
        "((queue_job_id IS NULL AND queue_handed_off_at IS NULL) OR "
        "(queue_job_id IS NOT NULL AND queue_handed_off_at IS NOT NULL))",
    )
    op.create_index(
        "ux_telegram_admin_broadcast_receipts_queue_job",
        "telegram_admin_broadcast_receipts",
        ["queue_job_id"],
        unique=True,
        postgresql_where=sa.text("queue_job_id IS NOT NULL"),
    )
    op.create_index(
        "ix_telegram_admin_broadcast_receipts_queue_handoff",
        "telegram_admin_broadcast_receipts",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('pending', 'retryable_failed') "
            "AND queue_job_id IS NULL AND worker_id IS NULL "
            "AND lease_until IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_admin_broadcast_receipts_queue_handoff",
        table_name="telegram_admin_broadcast_receipts",
    )
    op.drop_index(
        "ux_telegram_admin_broadcast_receipts_queue_job",
        table_name="telegram_admin_broadcast_receipts",
    )
    op.drop_constraint(
        "ck_telegram_admin_broadcast_receipts_queue_binding",
        "telegram_admin_broadcast_receipts",
        type_="check",
    )
    op.drop_constraint(
        "fk_telegram_admin_broadcast_receipts_queue_job",
        "telegram_admin_broadcast_receipts",
        type_="foreignkey",
    )
    op.drop_column("telegram_admin_broadcast_receipts", "queue_handed_off_at")
    op.drop_column("telegram_admin_broadcast_receipts", "queue_job_id")
    op.drop_index(
        "ix_telegram_admin_broadcasts_queue_fairness",
        table_name="telegram_admin_broadcasts",
    )
    op.drop_column("telegram_admin_broadcasts", "queue_last_handed_off_at")
