"""merge repeat-offer and Telegram queue migration heads

Revision ID: faa1b2c3d4e5
Revises: f9c4d5e6f7ae, f2c7d8e9a0b1
Create Date: 2026-07-18 09:00:00.000000
"""

from typing import Sequence, Union


revision: str = "faa1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = (
    "f9c4d5e6f7ae",
    "f2c7d8e9a0b1",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
