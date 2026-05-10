"""add chat member conversation state

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-10 07:10:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_members",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("chat_members", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "chat_members",
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("chat_members", sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_chat_members_user_hidden_pinned",
        "chat_members",
        ["user_id", "is_hidden", "is_pinned", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_members_user_hidden_pinned", table_name="chat_members")
    op.drop_column("chat_members", "hidden_at")
    op.drop_column("chat_members", "is_hidden")
    op.drop_column("chat_members", "pinned_at")
    op.drop_column("chat_members", "is_pinned")