"""add durable Telegram destination gate index

Revision ID: f4e9a0b1c2df
Revises: f3d8e9a0b1ce
Create Date: 2026-07-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4e9a0b1c2df"
down_revision: Union[str, Sequence[str], None] = "f3d8e9a0b1ce"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GATE_PREDICATE = (
    "(dispatch_started_at IS NOT NULL AND state IN "
    "('leased', 'ambiguous', 'ambiguous_unresolved', 'pending_reconcile')) OR "
    "(state = 'pending_retry' AND outcome_reason = 'telegram_rate_limited' "
    "AND next_retry_at IS NOT NULL) OR "
    "state = 'blocked_destination'"
)


def upgrade() -> None:
    op.create_index(
        "ix_telegram_delivery_jobs_destination_gate",
        "telegram_delivery_jobs",
        ["destination_key", "state", "next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(_GATE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_delivery_jobs_destination_gate",
        table_name="telegram_delivery_jobs",
    )
