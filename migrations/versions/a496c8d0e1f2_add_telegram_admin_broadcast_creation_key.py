"""add durable telegram admin broadcast creation key

Revision ID: a496c8d0e1f2
Revises: a385f6b7c8d0
Create Date: 2026-08-23 17:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a496c8d0e1f2"
down_revision: Union[str, Sequence[str], None] = "a385f6b7c8d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("creation_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "ux_telegram_admin_broadcasts_creation_key",
        "telegram_admin_broadcasts",
        ["creation_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "ux_telegram_admin_broadcasts_creation_key",
        "telegram_admin_broadcasts",
        type_="unique",
    )
    op.drop_column("telegram_admin_broadcasts", "creation_key")
