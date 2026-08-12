"""add requester_status_outbox_id for bot overtime status edits

Revision ID: d7f3e9a1b2c4
Revises: c6e2d8f0a1b3
Create Date: 2026-08-05 13:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7f3e9a1b2c4"
down_revision: Union[str, Sequence[str], None] = "c6e2d8f0a1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "offer_requests",
        sa.Column("requester_status_outbox_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("offer_requests", "requester_status_outbox_id")
