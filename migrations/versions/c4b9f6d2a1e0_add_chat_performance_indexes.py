"""add chat performance indexes

Revision ID: c4b9f6d2a1e0
Revises: ba0403512ae5
Create Date: 2026-04-23 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4b9f6d2a1e0'
down_revision: Union[str, None] = 'ba0403512ae5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_messages_conversation_window_active',
        'messages',
        ['sender_id', 'receiver_id', 'created_at', 'id'],
        unique=False,
        postgresql_where=sa.text('is_deleted = false'),
    )
    op.create_index(
        'ix_messages_unread_by_receiver_sender',
        'messages',
        ['receiver_id', 'sender_id'],
        unique=False,
        postgresql_where=sa.text('is_read = false'),
    )
    op.create_index(
        'ix_conversations_user1_last_message_at_compound',
        'conversations',
        ['user1_id', 'last_message_at'],
        unique=False,
    )
    op.create_index(
        'ix_conversations_user2_last_message_at_compound',
        'conversations',
        ['user2_id', 'last_message_at'],
        unique=False,
    )
    op.create_index(
        'ix_conversations_user1_unread_positive',
        'conversations',
        ['user1_id'],
        unique=False,
        postgresql_where=sa.text('unread_count_user1 > 0'),
    )
    op.create_index(
        'ix_conversations_user2_unread_positive',
        'conversations',
        ['user2_id'],
        unique=False,
        postgresql_where=sa.text('unread_count_user2 > 0'),
    )


def downgrade() -> None:
    op.drop_index('ix_conversations_user2_unread_positive', table_name='conversations')
    op.drop_index('ix_conversations_user1_unread_positive', table_name='conversations')
    op.drop_index('ix_conversations_user2_last_message_at_compound', table_name='conversations')
    op.drop_index('ix_conversations_user1_last_message_at_compound', table_name='conversations')
    op.drop_index('ix_messages_unread_by_receiver_sender', table_name='messages')
    op.drop_index('ix_messages_conversation_window_active', table_name='messages')