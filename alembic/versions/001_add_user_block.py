"""add user_block table and block fields

Revision ID: 001_add_user_block
Revises: 
Create Date: 2026-01-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001_add_user_block'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ===== 1. Create user_blocks table =====
    op.create_table(
        'user_blocks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('blocker_id', sa.Integer(), nullable=False),
        sa.Column('blocked_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['blocker_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['blocked_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('blocker_id', 'blocked_id', name='uq_blocker_blocked')
    )
    op.create_index('ix_user_blocks_blocker_id', 'user_blocks', ['blocker_id'], unique=False)
    op.create_index('ix_user_blocks_blocked_id', 'user_blocks', ['blocked_id'], unique=False)
    op.create_index('ix_user_blocks_id', 'user_blocks', ['id'], unique=False)
    
    # ===== 2. Add block fields to users table =====
    op.add_column('users', sa.Column('can_block_users', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('users', sa.Column('max_blocked_users', sa.Integer(), nullable=False, server_default='10'))


def downgrade() -> None:
    # ===== 1. Remove block fields from users =====
    op.drop_column('users', 'max_blocked_users')
    op.drop_column('users', 'can_block_users')
    
    # ===== 2. Drop user_blocks table =====
    op.drop_index('ix_user_blocks_id', table_name='user_blocks')
    op.drop_index('ix_user_blocks_blocked_id', table_name='user_blocks')
    op.drop_index('ix_user_blocks_blocker_id', table_name='user_blocks')
    op.drop_table('user_blocks')
