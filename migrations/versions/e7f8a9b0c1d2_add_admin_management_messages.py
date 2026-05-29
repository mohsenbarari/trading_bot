"""add admin management messages

Revision ID: e7f8a9b0c1d2
Revises: c9d0e1f2a3b4
Create Date: 2026-05-29 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "e7f8a9b0c1d2"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_market_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("reused_from_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("notified_recipients_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reused_from_id"], ["admin_market_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_market_messages_id"), "admin_market_messages", ["id"], unique=False)
    op.create_index(op.f("ix_admin_market_messages_created_by_id"), "admin_market_messages", ["created_by_id"], unique=False)
    op.create_index(op.f("ix_admin_market_messages_reused_from_id"), "admin_market_messages", ["reused_from_id"], unique=False)
    op.create_index(op.f("ix_admin_market_messages_is_active"), "admin_market_messages", ["is_active"], unique=False)
    op.create_index(op.f("ix_admin_market_messages_published_at"), "admin_market_messages", ["published_at"], unique=False)

    op.create_table(
        "admin_broadcast_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("target_groups", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("recipient_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_admin_broadcast_messages_id"), "admin_broadcast_messages", ["id"], unique=False)
    op.create_index(op.f("ix_admin_broadcast_messages_created_by_id"), "admin_broadcast_messages", ["created_by_id"], unique=False)
    op.create_index(op.f("ix_admin_broadcast_messages_published_at"), "admin_broadcast_messages", ["published_at"], unique=False)

    op.alter_column(
        "notifications",
        "message",
        existing_type=sa.String(),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "notifications",
        "message",
        existing_type=sa.Text(),
        type_=sa.String(),
        existing_nullable=False,
    )
    op.drop_index(op.f("ix_admin_broadcast_messages_published_at"), table_name="admin_broadcast_messages")
    op.drop_index(op.f("ix_admin_broadcast_messages_created_by_id"), table_name="admin_broadcast_messages")
    op.drop_index(op.f("ix_admin_broadcast_messages_id"), table_name="admin_broadcast_messages")
    op.drop_table("admin_broadcast_messages")
    op.drop_index(op.f("ix_admin_market_messages_published_at"), table_name="admin_market_messages")
    op.drop_index(op.f("ix_admin_market_messages_is_active"), table_name="admin_market_messages")
    op.drop_index(op.f("ix_admin_market_messages_reused_from_id"), table_name="admin_market_messages")
    op.drop_index(op.f("ix_admin_market_messages_created_by_id"), table_name="admin_market_messages")
    op.drop_index(op.f("ix_admin_market_messages_id"), table_name="admin_market_messages")
    op.drop_table("admin_market_messages")