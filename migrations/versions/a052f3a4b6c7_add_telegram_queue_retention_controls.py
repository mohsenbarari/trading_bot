"""add Telegram queue retention controls

Revision ID: a052f3a4b6c7
Revises: ff41e2f3a5b6
Create Date: 2026-07-19 06:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a052f3a4b6c7"
down_revision: Union[str, Sequence[str], None] = "ff41e2f3a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "retention_legal_hold",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column("retention_hold_reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "retention_hold_set_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_retention_hold",
        "telegram_delivery_jobs",
        "(retention_legal_hold = false AND "
        "retention_hold_reason_code IS NULL AND retention_hold_set_at IS NULL) OR "
        "(retention_legal_hold = true AND "
        "retention_hold_reason_code IS NOT NULL AND retention_hold_set_at IS NOT NULL)",
    )
    op.create_index(
        "ix_telegram_delivery_jobs_retention",
        "telegram_delivery_jobs",
        ["terminal_at", "id"],
        unique=False,
        postgresql_where=sa.text(
            "terminal_at IS NOT NULL AND retention_legal_hold = false"
        ),
    )
    op.add_column(
        "telegram_delivery_provider_outcomes",
        sa.Column(
            "payload_redacted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column(
        "telegram_delivery_provider_outcomes",
        "payload_redacted_at",
    )
    op.drop_index(
        "ix_telegram_delivery_jobs_retention",
        table_name="telegram_delivery_jobs",
    )
    op.drop_constraint(
        "ck_telegram_delivery_jobs_retention_hold",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.drop_column("telegram_delivery_jobs", "retention_hold_set_at")
    op.drop_column("telegram_delivery_jobs", "retention_hold_reason_code")
    op.drop_column("telegram_delivery_jobs", "retention_legal_hold")
