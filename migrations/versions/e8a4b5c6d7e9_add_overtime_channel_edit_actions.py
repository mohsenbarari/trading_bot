"""add overtime and final-tail channel edit telegram actions

Revision ID: e8a4b5c6d7e9
Revises: d7f3e9a1b2c4
Create Date: 2026-08-05 14:30:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e8a4b5c6d7e9"
down_revision: Union[str, Sequence[str], None] = "d7f3e9a1b2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum values remain on downgrade for forward-compatible application
    # rollback; PostgreSQL cannot safely remove used enum values in place.
    # PostgreSQL requires a commit before new enum values can be referenced by
    # a later migration in the same Alembic invocation.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
            "'overtime_channel_edit'"
        )
        op.execute(
            "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
            "'final_tail_channel_edit'"
        )


def downgrade() -> None:
    # Intentionally empty: PostgreSQL cannot drop enum values in place.
    pass
