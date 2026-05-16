"""add single session recovery requests

Revision ID: e9f8d7c6b5a4
Revises: d7c8e9f0a1b2
Create Date: 2026-05-16 12:15:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "e9f8d7c6b5a4"
down_revision: Union[str, Sequence[str], None] = "d7c8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


recovery_status_enum = postgresql.ENUM(
    "pending_admin_review",
    "identity_verification_requested",
    "identity_submitted",
    "approved",
    "rejected",
    "cancelled",
    "expired",
    name="singlesessionrecoverystatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    recovery_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "single_session_recovery_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_login_request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requester_device_name", sa.String(length=255), nullable=False),
        sa.Column("requester_ip", sa.String(length=45), nullable=True),
        sa.Column("status", recovery_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("inline_action_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chat_action_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("identity_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("identity_submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_login_request_id"], ["session_login_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_single_session_recovery_requests_user_id"),
        "single_session_recovery_requests",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_single_session_recovery_requests_session_login_request_id"),
        "single_session_recovery_requests",
        ["session_login_request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_single_session_recovery_requests_status"),
        "single_session_recovery_requests",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_single_session_recovery_requests_status"), table_name="single_session_recovery_requests")
    op.drop_index(op.f("ix_single_session_recovery_requests_session_login_request_id"), table_name="single_session_recovery_requests")
    op.drop_index(op.f("ix_single_session_recovery_requests_user_id"), table_name="single_session_recovery_requests")
    op.drop_table("single_session_recovery_requests")
    bind = op.get_bind()
    recovery_status_enum.drop(bind, checkfirst=True)