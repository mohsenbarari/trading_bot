"""add change log delivery isolation state

Revision ID: c7d8e9f0a1b4
Revises: b9e0f1a2c3d4
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b4"
down_revision: Union[str, Sequence[str], None] = "b9e0f1a2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "change_log",
        sa.Column(
            "delivery_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "change_log",
        sa.Column("last_delivery_error", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "change_log",
        sa.Column("last_delivery_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "change_log",
        sa.Column("next_delivery_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "change_log",
        sa.Column("quarantined_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_change_log_delivery_attempt_count_nonnegative",
        "change_log",
        "delivery_attempt_count >= 0",
    )
    op.create_index(
        "idx_change_log_delivery_ready",
        "change_log",
        [
            "synced",
            "quarantined_at",
            "next_delivery_attempt_at",
            "id",
        ],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_change_log_delivery_ready", table_name="change_log")
    op.drop_constraint(
        "ck_change_log_delivery_attempt_count_nonnegative",
        "change_log",
        type_="check",
    )
    op.drop_column("change_log", "quarantined_at")
    op.drop_column("change_log", "next_delivery_attempt_at")
    op.drop_column("change_log", "last_delivery_attempt_at")
    op.drop_column("change_log", "last_delivery_error")
    op.drop_column("change_log", "delivery_attempt_count")
