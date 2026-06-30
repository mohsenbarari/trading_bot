"""add bot onboarding steps

Revision ID: f5c6d7e8f9a1
Revises: f4b5c6d7e8f0
Create Date: 2026-06-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f5c6d7e8f9a1"
down_revision: Union[str, Sequence[str], None] = "f4b5c6d7e8f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("bot_onboarding_required_step", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "users",
        sa.Column("bot_onboarding_completed_step", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("users", sa.Column("bot_onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "bot_onboarding_completed_at")
    op.drop_column("users", "bot_onboarding_completed_step")
    op.drop_column("users", "bot_onboarding_required_step")
