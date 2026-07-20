"""merge three-site DR and Telegram queue migration heads

Revision ID: b320c1d2e3f4
Revises: a274f5a6b8c9, e9e4f5a6b7c8
Create Date: 2026-07-20 03:00:00.000000
"""

from typing import Sequence, Union


revision: str = "b320c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = (
    "a274f5a6b8c9",
    "e9e4f5a6b7c8",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
