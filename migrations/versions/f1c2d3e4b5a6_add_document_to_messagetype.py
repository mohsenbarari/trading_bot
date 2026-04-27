"""add document to messagetype

Revision ID: f1c2d3e4b5a6
Revises: c4b9f6d2a1e0
Create Date: 2026-04-27 09:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f1c2d3e4b5a6'
down_revision: Union[str, None] = 'c4b9f6d2a1e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'DOCUMENT'")


def downgrade() -> None:
    pass