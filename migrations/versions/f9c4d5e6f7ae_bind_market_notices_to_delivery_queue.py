"""bind market channel notices to the shared Telegram delivery queue

Revision ID: f9c4d5e6f7ae
Revises: f8b3c4d5e6fd
Create Date: 2026-07-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9c4d5e6f7ae"
down_revision: Union[str, Sequence[str], None] = "f8b3c4d5e6fd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "market_channel_notice_receipts",
        sa.Column("queue_job_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "market_channel_notice_receipts",
        sa.Column("queue_handed_off_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "market_channel_notice_receipts",
        sa.Column(
            "queue_reconciliation_required_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_market_channel_notice_receipts_queue_job",
        "market_channel_notice_receipts",
        "telegram_delivery_jobs",
        ["queue_job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_market_channel_notice_receipts_queue_binding",
        "market_channel_notice_receipts",
        "((queue_job_id IS NULL AND queue_handed_off_at IS NULL) OR "
        "(queue_job_id IS NOT NULL AND queue_handed_off_at IS NOT NULL))",
    )
    op.create_check_constraint(
        "ck_market_channel_notice_receipts_queue_owner",
        "market_channel_notice_receipts",
        "NOT (queue_job_id IS NOT NULL AND "
        "queue_reconciliation_required_at IS NOT NULL)",
    )
    op.create_index(
        "ux_market_channel_notice_receipts_queue_job",
        "market_channel_notice_receipts",
        ["queue_job_id"],
        unique=True,
        postgresql_where=sa.text("queue_job_id IS NOT NULL"),
    )
    op.create_index(
        "ix_market_channel_notice_receipts_queue_handoff",
        "market_channel_notice_receipts",
        ["next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "status IN ('pending', 'failed') "
            "AND queue_job_id IS NULL "
            "AND queue_reconciliation_required_at IS NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_channel_notice_receipts_queue_handoff",
        table_name="market_channel_notice_receipts",
    )
    op.drop_index(
        "ux_market_channel_notice_receipts_queue_job",
        table_name="market_channel_notice_receipts",
    )
    op.drop_constraint(
        "ck_market_channel_notice_receipts_queue_owner",
        "market_channel_notice_receipts",
        type_="check",
    )
    op.drop_constraint(
        "ck_market_channel_notice_receipts_queue_binding",
        "market_channel_notice_receipts",
        type_="check",
    )
    op.drop_constraint(
        "fk_market_channel_notice_receipts_queue_job",
        "market_channel_notice_receipts",
        type_="foreignkey",
    )
    op.drop_column(
        "market_channel_notice_receipts",
        "queue_reconciliation_required_at",
    )
    op.drop_column("market_channel_notice_receipts", "queue_handed_off_at")
    op.drop_column("market_channel_notice_receipts", "queue_job_id")
