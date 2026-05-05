"""add generic chat foundation

Revision ID: b6d7e8f9a0b1
Revises: f7e8d9c0b1a2
Create Date: 2026-05-05 10:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6d7e8f9a0b1'
down_revision: Union[str, None] = 'f7e8d9c0b1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chats',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('type', sa.Enum('DIRECT', 'GROUP', 'CHANNEL', name='chattype'), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('is_system', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_mandatory', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('is_deleted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_members', sa.Integer(), nullable=True),
        sa.Column('last_message_id', sa.Integer(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['created_by_id'], ['users.id']),
        sa.ForeignKeyConstraint(['last_message_id'], ['messages.id'], name='fk_chats_last_message'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_chats_id'), 'chats', ['id'], unique=False)
    op.create_index(op.f('ix_chats_type'), 'chats', ['type'], unique=False)
    op.create_index(op.f('ix_chats_created_by_id'), 'chats', ['created_by_id'], unique=False)
    op.create_index(op.f('ix_chats_last_message_at'), 'chats', ['last_message_at'], unique=False)

    op.add_column('messages', sa.Column('chat_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_messages_chat', 'messages', 'chats', ['chat_id'], ['id'])
    op.create_index(op.f('ix_messages_chat_id'), 'messages', ['chat_id'], unique=False)
    op.create_index(
        'ix_messages_chat_window_active',
        'messages',
        ['chat_id', 'created_at', 'id'],
        unique=False,
        postgresql_where=sa.text('is_deleted = false'),
    )
    op.create_index(
        'ix_messages_chat_sender_active',
        'messages',
        ['chat_id', 'sender_id', 'created_at'],
        unique=False,
    )

    op.create_table(
        'chat_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('ADMIN', 'MEMBER', name='chatmemberrole'), nullable=False),
        sa.Column('membership_status', sa.Enum('ACTIVE', 'LEFT', 'REMOVED', 'INACTIVE', name='chatmembershipstatus'), nullable=False),
        sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_read_message_id', sa.Integer(), nullable=True),
        sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_muted', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['chat_id'], ['chats.id'], name='fk_chat_members_chat'),
        sa.ForeignKeyConstraint(['last_read_message_id'], ['messages.id'], name='fk_chat_members_last_read_message'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_chat_members_user'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_chat_members_id'), 'chat_members', ['id'], unique=False)
    op.create_index(op.f('ix_chat_members_chat_id'), 'chat_members', ['chat_id'], unique=False)
    op.create_index(op.f('ix_chat_members_user_id'), 'chat_members', ['user_id'], unique=False)
    op.create_index(
        'ux_chat_members_active_membership',
        'chat_members',
        ['chat_id', 'user_id'],
        unique=True,
        postgresql_where=sa.text("membership_status = 'ACTIVE'"),
    )
    op.create_index(
        'ix_chat_members_user_status_updated',
        'chat_members',
        ['user_id', 'membership_status', 'updated_at'],
        unique=False,
    )
    op.create_index(
        'ix_chat_members_chat_status_role',
        'chat_members',
        ['chat_id', 'membership_status', 'role'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_chat_members_chat_status_role', table_name='chat_members')
    op.drop_index('ix_chat_members_user_status_updated', table_name='chat_members')
    op.drop_index('ux_chat_members_active_membership', table_name='chat_members')
    op.drop_index(op.f('ix_chat_members_user_id'), table_name='chat_members')
    op.drop_index(op.f('ix_chat_members_chat_id'), table_name='chat_members')
    op.drop_index(op.f('ix_chat_members_id'), table_name='chat_members')
    op.drop_table('chat_members')

    op.drop_index('ix_messages_chat_sender_active', table_name='messages')
    op.drop_index('ix_messages_chat_window_active', table_name='messages')
    op.drop_index(op.f('ix_messages_chat_id'), table_name='messages')
    op.drop_constraint('fk_messages_chat', 'messages', type_='foreignkey')
    op.drop_column('messages', 'chat_id')

    op.drop_index(op.f('ix_chats_last_message_at'), table_name='chats')
    op.drop_index(op.f('ix_chats_created_by_id'), table_name='chats')
    op.drop_index(op.f('ix_chats_type'), table_name='chats')
    op.drop_index(op.f('ix_chats_id'), table_name='chats')
    op.drop_table('chats')

    op.execute('DROP TYPE IF EXISTS chatmembershipstatus')
    op.execute('DROP TYPE IF EXISTS chatmemberrole')
    op.execute('DROP TYPE IF EXISTS chattype')