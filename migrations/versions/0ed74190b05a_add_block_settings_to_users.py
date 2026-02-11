"""add_block_settings_to_users

Revision ID: 0ed74190b05a
Revises: 862d9168012a
Create Date: 2026-01-28 10:37:58.808876

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector


# revision identifiers, used by Alembic.
revision: str = '0ed74190b05a'
down_revision: Union[str, None] = '862d9168012a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if table exists
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    
    if 'user_blocks' not in tables:
        # Create table if missing
        op.create_table(
            'user_blocks',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('blocker_id', sa.Integer(), nullable=False),
            sa.Column('blocked_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(['blocker_id'], ['users.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['blocked_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('blocker_id', 'blocked_id', name='uq_blocker_blocked')
        )
        op.create_index(op.f('ix_user_blocks_id'), 'user_blocks', ['id'], unique=False)
        op.create_index(op.f('ix_user_blocks_blocker_id'), 'user_blocks', ['blocker_id'], unique=False)
        op.create_index(op.f('ix_user_blocks_blocked_id'), 'user_blocks', ['blocked_id'], unique=False)
    else:
        # If table exists, apply changes as originally intended (safely)
        constraints = [c['name'] for c in inspector.get_unique_constraints('user_blocks')]
        
        if 'user_blocks_blocker_id_blocked_id_key' in constraints:
            op.drop_constraint('user_blocks_blocker_id_blocked_id_key', 'user_blocks', type_='unique')
            
        if 'uq_blocker_blocked' not in constraints:
            op.create_unique_constraint('uq_blocker_blocked', 'user_blocks', ['blocker_id', 'blocked_id'])
            
        indexes = [i['name'] for i in inspector.get_indexes('user_blocks')]
        if 'ix_user_blocks_id' not in indexes:
             op.create_index(op.f('ix_user_blocks_id'), 'user_blocks', ['id'], unique=False)


def downgrade() -> None:
    # Check if table exists
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()
    
    if 'user_blocks' in tables:
        # Revert changes safely
        constraints = [c['name'] for c in inspector.get_unique_constraints('user_blocks')]
        
        if 'uq_blocker_blocked' in constraints:
            op.drop_constraint('uq_blocker_blocked', 'user_blocks', type_='unique')
            
        # Try to restore old constraint if not exists
        if 'user_blocks_blocker_id_blocked_id_key' not in constraints:
             op.create_unique_constraint('user_blocks_blocker_id_blocked_id_key', 'user_blocks', ['blocker_id', 'blocked_id'])
        
        indexes = [i['name'] for i in inspector.get_indexes('user_blocks')]
        if 'ix_user_blocks_id' in indexes:
            op.drop_index(op.f('ix_user_blocks_id'), table_name='user_blocks')
