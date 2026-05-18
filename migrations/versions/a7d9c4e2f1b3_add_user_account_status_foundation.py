"""add user account status foundation

Revision ID: a7d9c4e2f1b3
Revises: f4c3b2a1d0e9
Create Date: 2026-05-18 11:55:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "a7d9c4e2f1b3"
down_revision: Union[str, None] = "f4c3b2a1d0e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_account_status = ENUM(
        "active",
        "inactive",
        name="useraccountstatus",
        create_type=False,
    )
    user_account_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "account_status",
            user_account_status,
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column("users", sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("messenger_grace_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("messenger_blocked_at", sa.DateTime(timezone=True), nullable=True))

    op.create_index(op.f("ix_users_account_status"), "users", ["account_status"], unique=False)
    op.create_index(
        op.f("ix_users_messenger_grace_expires_at"),
        "users",
        ["messenger_grace_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_users_messenger_grace_expires_at"), table_name="users")
    op.drop_index(op.f("ix_users_account_status"), table_name="users")

    op.drop_column("users", "messenger_blocked_at")
    op.drop_column("users", "messenger_grace_expires_at")
    op.drop_column("users", "deactivated_at")
    op.drop_column("users", "account_status")

    ENUM("active", "inactive", name="useraccountstatus", create_type=False).drop(
        op.get_bind(),
        checkfirst=True,
    )