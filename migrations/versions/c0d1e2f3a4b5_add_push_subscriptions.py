"""add push subscriptions

Revision ID: c0d1e2f3a4b5
Revises: b1c2d3e4f5a6
Create Date: 2026-06-16 11:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, Sequence[str], None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("platform", sa.String(length=80), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_push_subscriptions_id"), "push_subscriptions", ["id"], unique=False)
    op.create_index(
        "ix_push_subscriptions_endpoint_hash",
        "push_subscriptions",
        ["endpoint_hash"],
        unique=True,
    )
    op.create_index(
        "ix_push_subscriptions_user_enabled",
        "push_subscriptions",
        ["user_id", "enabled"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_user_enabled", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_endpoint_hash", table_name="push_subscriptions")
    op.drop_index(op.f("ix_push_subscriptions_id"), table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
