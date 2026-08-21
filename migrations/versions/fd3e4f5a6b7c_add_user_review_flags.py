"""add extensible user review flags

Revision ID: fd3e4f5a6b7c
Revises: fc2d3e4f5a6b
Create Date: 2026-08-21 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fd3e4f5a6b7c"
down_revision: Union[str, Sequence[str], None] = "fc2d3e4f5a6b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("flag_type", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'open'"), nullable=False),
        sa.Column("severity", sa.String(length=24), server_default=sa.text("'warning'"), nullable=False),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("trigger_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("first_flagged_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_flagged_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('open', 'resolved', 'dismissed')", name="ck_user_flags_status"),
        sa.CheckConstraint("trigger_count >= 1", name="ck_user_flags_trigger_count_positive"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_flags_status_last_flagged", "user_flags", ["status", "last_flagged_at"])
    op.create_index("ix_user_flags_user_type", "user_flags", ["user_id", "flag_type"])
    op.create_index(
        "ux_user_flags_open_user_type",
        "user_flags",
        ["user_id", "flag_type"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ux_user_flags_open_user_type", table_name="user_flags")
    op.drop_index("ix_user_flags_user_type", table_name="user_flags")
    op.drop_index("ix_user_flags_status_last_flagged", table_name="user_flags")
    op.drop_table("user_flags")
