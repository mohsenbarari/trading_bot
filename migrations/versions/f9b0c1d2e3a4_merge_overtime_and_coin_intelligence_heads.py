"""merge overtime and coin-intelligence migration heads

Revision ID: f9b0c1d2e3a4
Revises: e8a4b5c6d7e9, e5a1c4d7b2f9
Create Date: 2026-08-05 16:40:00.000000
"""

from typing import Sequence, Union


revision: str = "f9b0c1d2e3a4"
down_revision: Union[str, Sequence[str], None] = (
    "e8a4b5c6d7e9",
    "e5a1c4d7b2f9",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Schema-only merge of the overtime and coin-intelligence additive heads.
    pass


def downgrade() -> None:
    pass
