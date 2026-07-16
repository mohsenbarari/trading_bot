"""add telegram monitoring publication surface

Revision ID: f2c7d8e9f0b3
Revises: f1b6e7f8a9dc
Create Date: 2026-07-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f2c7d8e9f0b3"
down_revision: Union[str, Sequence[str], None] = "f1b6e7f8a9dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE offerpublicationsurface ADD VALUE IF NOT EXISTS 'telegram_monitoring_channel'")


def downgrade() -> None:
    pass
