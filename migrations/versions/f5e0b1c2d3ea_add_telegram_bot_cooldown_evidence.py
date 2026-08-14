"""add durable Telegram bot cooldown evidence

Revision ID: f5e0b1c2d3ea
Revises: f4e9a0b1c2df
Create Date: 2026-07-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5e0b1c2d3ea"
down_revision: Union[str, Sequence[str], None] = "f4e9a0b1c2df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "rate_limit_probe",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "last_rate_limited_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "last_rate_limit_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "bot_cooldown_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_hard_pause_gate",
        "telegram_delivery_jobs",
        ["state", "bot_identity", "destination_key", "id"],
        unique=False,
        postgresql_where=sa.text(
            "state IN ('blocked_destination', 'blocked_bot', 'blocked_gateway')"
        ),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_bot_cooldown",
        "telegram_delivery_jobs",
        ["bot_identity", "bot_cooldown_until", "id"],
        unique=False,
        postgresql_where=sa.text("bot_cooldown_until IS NOT NULL"),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_recent_rate_limit",
        "telegram_delivery_jobs",
        ["bot_identity", "last_rate_limited_at", "destination_key", "id"],
        unique=False,
        postgresql_where=sa.text("last_rate_limited_at IS NOT NULL"),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_bot_probe_gate",
        "telegram_delivery_jobs",
        ["bot_identity", "state", "lease_until", "id"],
        unique=False,
        postgresql_where=sa.text(
            "rate_limit_probe = true AND dispatch_started_at IS NOT NULL AND "
            "state IN ('leased', 'ambiguous', 'ambiguous_unresolved', "
            "'pending_reconcile')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_delivery_jobs_bot_probe_gate",
        table_name="telegram_delivery_jobs",
    )
    op.drop_index(
        "ix_telegram_delivery_jobs_recent_rate_limit",
        table_name="telegram_delivery_jobs",
    )
    op.drop_index(
        "ix_telegram_delivery_jobs_bot_cooldown",
        table_name="telegram_delivery_jobs",
    )
    op.drop_index(
        "ix_telegram_delivery_jobs_hard_pause_gate",
        table_name="telegram_delivery_jobs",
    )
    op.drop_column("telegram_delivery_jobs", "bot_cooldown_until")
    op.drop_column("telegram_delivery_jobs", "last_rate_limit_until")
    op.drop_column("telegram_delivery_jobs", "last_rate_limited_at")
    op.drop_column("telegram_delivery_jobs", "rate_limit_probe")
