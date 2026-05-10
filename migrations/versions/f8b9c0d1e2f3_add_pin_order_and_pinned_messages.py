"""add pin order and pinned messages

Revision ID: f8b9c0d1e2f3
Revises: f6a7b8c9d0e1
Create Date: 2026-05-10 09:35:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f8b9c0d1e2f3"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chat_members", sa.Column("pin_order", sa.Integer(), nullable=True))
    op.create_index(
        "ix_chat_members_user_pinned_order",
        "chat_members",
        ["user_id", "is_pinned", "pin_order"],
        unique=False,
    )

    op.add_column("chats", sa.Column("pinned_message_id", sa.Integer(), nullable=True))
    op.add_column("chats", sa.Column("pinned_message_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("chats", sa.Column("pinned_message_by_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_chats_pinned_message", "chats", "messages", ["pinned_message_id"], ["id"])
    op.create_foreign_key("fk_chats_pinned_message_by", "chats", "users", ["pinned_message_by_id"], ["id"])

    op.execute(
        """
        WITH ranked AS (
            SELECT
                cm.id,
                ROW_NUMBER() OVER (
                    PARTITION BY cm.user_id
                    ORDER BY COALESCE(cm.pinned_at, cm.updated_at, cm.created_at, cm.joined_at) ASC, cm.id ASC
                ) AS seq
            FROM chat_members cm
            JOIN chats c ON c.id = cm.chat_id
            WHERE cm.is_pinned = true
              AND cm.membership_status = 'active'
              AND COALESCE(c.is_mandatory, false) = false
              AND COALESCE(c.is_deleted, false) = false
        )
        UPDATE chat_members AS cm
        SET pin_order = ranked.seq
        FROM ranked
        WHERE cm.id = ranked.id
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_chats_pinned_message_by", "chats", type_="foreignkey")
    op.drop_constraint("fk_chats_pinned_message", "chats", type_="foreignkey")
    op.drop_column("chats", "pinned_message_by_id")
    op.drop_column("chats", "pinned_message_at")
    op.drop_column("chats", "pinned_message_id")

    op.drop_index("ix_chat_members_user_pinned_order", table_name="chat_members")
    op.drop_column("chat_members", "pin_order")