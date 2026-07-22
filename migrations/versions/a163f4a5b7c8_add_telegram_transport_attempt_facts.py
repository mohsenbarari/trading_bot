"""add Telegram transport-phase and provider-attempt facts

Revision ID: a163f4a5b7c8
Revises: a052f3a4b6c7
Create Date: 2026-07-19 07:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a163f4a5b7c8"
down_revision: Union[str, Sequence[str], None] = "a052f3a4b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column(
            "provider_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_provider_attempt_count",
        "telegram_delivery_jobs",
        "provider_attempt_count >= 0",
    )
    op.add_column(
        "telegram_delivery_provider_outcomes",
        sa.Column("transport_phase", sa.String(length=24), nullable=True),
    )
    op.create_check_constraint(
        "ck_telegram_delivery_provider_outcomes_transport_phase",
        "telegram_delivery_provider_outcomes",
        "transport_phase IS NULL OR transport_phase IN "
        "('pre_write', 'write_unknown', 'response_received')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_telegram_delivery_provider_outcomes_transport_phase",
        "telegram_delivery_provider_outcomes",
        type_="check",
    )
    op.drop_column("telegram_delivery_provider_outcomes", "transport_phase")
    op.drop_constraint(
        "ck_telegram_delivery_jobs_provider_attempt_count",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.drop_column("telegram_delivery_jobs", "provider_attempt_count")
