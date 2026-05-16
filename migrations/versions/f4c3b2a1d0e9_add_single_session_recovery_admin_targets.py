"""add single-session recovery admin targets

Revision ID: f4c3b2a1d0e9
Revises: e9f8d7c6b5a4
Create Date: 2026-05-16 18:40:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f4c3b2a1d0e9"
down_revision = "e9f8d7c6b5a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "single_session_recovery_admin_targets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recovery_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=False),
        sa.Column("current_action_message_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["current_action_message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recovery_request_id"], ["single_session_recovery_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recovery_request_id",
            "admin_user_id",
            name="uq_single_session_recovery_admin_targets_recovery_admin",
        ),
    )
    op.create_index(
        op.f("ix_single_session_recovery_admin_targets_recovery_request_id"),
        "single_session_recovery_admin_targets",
        ["recovery_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_single_session_recovery_admin_targets_admin_user_id"),
        "single_session_recovery_admin_targets",
        ["admin_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_single_session_recovery_admin_targets_current_action_message_id"),
        "single_session_recovery_admin_targets",
        ["current_action_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_single_session_recovery_admin_targets_current_action_message_id"),
        table_name="single_session_recovery_admin_targets",
    )
    op.drop_index(
        op.f("ix_single_session_recovery_admin_targets_admin_user_id"),
        table_name="single_session_recovery_admin_targets",
    )
    op.drop_index(
        op.f("ix_single_session_recovery_admin_targets_recovery_request_id"),
        table_name="single_session_recovery_admin_targets",
    )
    op.drop_table("single_session_recovery_admin_targets")