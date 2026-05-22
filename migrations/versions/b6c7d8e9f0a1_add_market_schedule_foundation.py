"""add market schedule foundation

Revision ID: b6c7d8e9f0a1
Revises: c3d4e5f6a7b0
Create Date: 2026-05-22 08:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "b6c7d8e9f0a1"
down_revision: Union[str, None] = "c3d4e5f6a7b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


market_schedule_override_type = ENUM(
    "closed_all_day",
    "open_all_day",
    "custom_hours",
    name="marketscheduleoverridetype",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    market_schedule_override_type.create(bind, checkfirst=True)

    op.create_table(
        "market_schedule_overrides",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("override_type", market_schedule_override_type, nullable=False),
        sa.Column("open_time_local", sa.Time(timezone=False), nullable=True),
        sa.Column("close_time_local", sa.Time(timezone=False), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_market_schedule_overrides_id"), "market_schedule_overrides", ["id"], unique=False)
    op.create_index("ux_market_schedule_overrides_date", "market_schedule_overrides", ["date"], unique=True)
    op.create_index(
        "ix_market_schedule_overrides_override_type",
        "market_schedule_overrides",
        ["override_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_market_schedule_overrides_created_by_user_id"),
        "market_schedule_overrides",
        ["created_by_user_id"],
        unique=False,
    )

    op.create_table(
        "market_runtime_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("is_open", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("active_web_notice_visible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("offers_since_last_open", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_transition_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_market_runtime_state_id"), "market_runtime_state", ["id"], unique=False)
    op.create_index(
        op.f("ix_market_runtime_state_last_transition_at"),
        "market_runtime_state",
        ["last_transition_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_market_runtime_state_last_transition_at"), table_name="market_runtime_state")
    op.drop_index(op.f("ix_market_runtime_state_id"), table_name="market_runtime_state")
    op.drop_table("market_runtime_state")

    op.drop_index(op.f("ix_market_schedule_overrides_created_by_user_id"), table_name="market_schedule_overrides")
    op.drop_index("ix_market_schedule_overrides_override_type", table_name="market_schedule_overrides")
    op.drop_index("ux_market_schedule_overrides_date", table_name="market_schedule_overrides")
    op.drop_index(op.f("ix_market_schedule_overrides_id"), table_name="market_schedule_overrides")
    op.drop_table("market_schedule_overrides")

    bind = op.get_bind()
    market_schedule_override_type.drop(bind, checkfirst=True)