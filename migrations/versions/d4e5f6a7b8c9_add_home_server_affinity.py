"""add home server affinity

Revision ID: d4e5f6a7b8c9
Revises: b6d7e8f9a0b1
Create Date: 2026-05-07 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'b6d7e8f9a0b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('home_server', sa.String(length=16), server_default='foreign', nullable=False))
    op.add_column('offers', sa.Column('home_server', sa.String(length=16), server_default='foreign', nullable=False))
    op.add_column('user_sessions', sa.Column('home_server', sa.String(length=16), server_default='foreign', nullable=False))
    op.add_column('session_login_requests', sa.Column('requester_home_server', sa.String(length=16), server_default='foreign', nullable=False))
    op.create_index(op.f('ix_offers_home_server'), 'offers', ['home_server'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_offers_home_server'), table_name='offers')
    op.drop_column('session_login_requests', 'requester_home_server')
    op.drop_column('user_sessions', 'home_server')
    op.drop_column('offers', 'home_server')
    op.drop_column('users', 'home_server')
