"""add durable global ordering key for Telegram Offer edits

Revision ID: fe30d1e2f4b5
Revises: fd29c0e1f3a4
Create Date: 2026-07-19 03:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fe30d1e2f4b5"
down_revision: Union[str, Sequence[str], None] = "fd29c0e1f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column("source_order_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing foreign-local rows are upgraded without requiring a synchronized
    # Offer match. The domain timestamp is preferred; job creation time is a
    # deterministic compatibility fallback for orphaned historical rows.
    op.execute(
        """
        UPDATE telegram_delivery_jobs AS job
        SET source_order_at = COALESCE(offer.created_at, job.created_at)
        FROM offers AS offer
        WHERE job.feeder_kind = 'offer_edit'
          AND offer.offer_public_id = job.source_natural_id
          AND job.source_order_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE telegram_delivery_jobs
        SET source_order_at = created_at
        WHERE feeder_kind = 'offer_edit' AND source_order_at IS NULL
        """
    )
    op.create_index(
        "ix_telegram_delivery_jobs_offer_edit_order",
        "telegram_delivery_jobs",
        [
            "bot_identity",
            "priority",
            "priority_rank",
            sa.text("source_order_at DESC"),
            "enqueued_seq",
        ],
        unique=False,
        postgresql_where=sa.text(
            "state IN ('pending', 'pending_retry') AND feeder_kind = 'offer_edit'"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_delivery_jobs_offer_edit_order",
        table_name="telegram_delivery_jobs",
    )
    op.drop_column("telegram_delivery_jobs", "source_order_at")
