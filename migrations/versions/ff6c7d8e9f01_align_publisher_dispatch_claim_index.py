"""align publisher dispatch claim index with live claim predicate

Revision ID: ff6c7d8e9f01
Revises: ff5a6b7c8d9e
Create Date: 2026-08-23 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ff6c7d8e9f01"
down_revision: Union[str, Sequence[str], None] = "ff5a6b7c8d9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE = "telegram_publisher_dispatch_commands"
_INDEX = "ix_telegram_publisher_dispatch_commands_claim"
_NEW_PREDICATE = "state IN ('pending', 'retry_due', 'sent')"
_OLD_PREDICATE = "state IN ('pending', 'retry_due')"


def upgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(
        _INDEX,
        _TABLE,
        ["id"],
        unique=False,
        postgresql_where=sa.text(_NEW_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.create_index(
        _INDEX,
        _TABLE,
        ["state", "next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text(_OLD_PREDICATE),
    )
