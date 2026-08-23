"""add publisher lane heartbeats for local B2B acknowledgement

Revision ID: ff7d8e9f0a12
Revises: ff6c7d8e9f01
Create Date: 2026-08-23 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "ff7d8e9f0a12"
down_revision: Union[str, Sequence[str], None] = "ff6c7d8e9f01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_publisher_lane_heartbeats",
        sa.Column("publisher_bot_identity", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "publisher_bot_identity IN ("
            "'publisher_1', 'publisher_2', 'publisher_3', "
            "'publisher_4', 'publisher_5')",
            name="ck_telegram_publisher_lane_heartbeats_publisher",
        ),
        sa.PrimaryKeyConstraint("publisher_bot_identity"),
    )


def downgrade() -> None:
    op.drop_table("telegram_publisher_lane_heartbeats")
