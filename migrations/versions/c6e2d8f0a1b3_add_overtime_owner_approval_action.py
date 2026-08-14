"""add overtime_owner_approval telegram delivery action

Revision ID: c6e2d8f0a1b3
Revises: b5d1c7e93f04
Create Date: 2026-08-05 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c6e2d8f0a1b3"
down_revision: Union[str, Sequence[str], None] = "b5d1c7e93f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum values remain on downgrade for forward-compatible application
    # rollback; PostgreSQL cannot safely remove used enum values in place.
    # PostgreSQL requires a commit before a new enum value can be referenced by
    # a later migration in the same Alembic invocation.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
            "'overtime_owner_approval'"
        )


def downgrade() -> None:
    # Intentionally empty: PostgreSQL cannot drop enum values in place.
    pass
