"""add avatar file ids to users and chats

Revision ID: a9b8c7d6e5f4
Revises: f8b9c0d1e2f3
Create Date: 2026-05-10 13:05:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9b8c7d6e5f4'
down_revision = 'f8b9c0d1e2f3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('avatar_file_id', sa.String(length=36), nullable=True))
    op.add_column('chats', sa.Column('avatar_file_id', sa.String(length=36), nullable=True))
    op.create_foreign_key(
        'fk_users_avatar_file_id_chat_files',
        'users',
        'chat_files',
        ['avatar_file_id'],
        ['id'],
    )
    op.create_foreign_key(
        'fk_chats_avatar_file_id_chat_files',
        'chats',
        'chat_files',
        ['avatar_file_id'],
        ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_chats_avatar_file_id_chat_files', 'chats', type_='foreignkey')
    op.drop_constraint('fk_users_avatar_file_id_chat_files', 'users', type_='foreignkey')
    op.drop_column('chats', 'avatar_file_id')
    op.drop_column('users', 'avatar_file_id')