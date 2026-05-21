"""add mentions to messages

Revision ID: c3d4e5f6a7b0
Revises: b2c3d4e5f6a8
Create Date: 2026-05-20 20:50:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b0"
down_revision: Union[str, None] = "b2c3d4e5f6a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("mentions", sa.JSON(), server_default="[]", nullable=False))
    op.add_column("messages", sa.Column("mention_all", sa.Boolean(), server_default="false", nullable=False))


def downgrade() -> None:
    op.drop_column("messages", "mention_all")
    op.drop_column("messages", "mentions")
