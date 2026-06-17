"""backfill offer expired_at

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-06-17 10:24:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE offers
        SET expired_at = COALESCE(updated_at, created_at)
        WHERE status = 'EXPIRED'
          AND expired_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE offers
        SET expired_at = NULL
        WHERE status = 'EXPIRED'
          AND expire_reason IS NULL
        """
    )
