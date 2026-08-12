"""retain the latest unstarted Telegram queue deferral for audit evidence

Revision ID: f9c8d7e6a5b4
Revises: f9b0c1d2e3a4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9c8d7e6a5b4"
down_revision: Union[str, Sequence[str], None] = "f9b0c1d2e3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_delivery_jobs",
        sa.Column("last_unstarted_defer_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_delivery_jobs", "last_unstarted_defer_until")
