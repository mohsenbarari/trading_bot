"""merge deployed queue-deferral evidence with publisher dispatch outbox

Revision ID: fa0b1c2d3e4f
Revises: f9a0b1c2d3e4, f9c8d7e6a5b4
"""
from typing import Sequence, Union


revision: str = "fa0b1c2d3e4f"
down_revision: Union[str, Sequence[str], None] = (
    "f9a0b1c2d3e4",
    "f9c8d7e6a5b4",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
