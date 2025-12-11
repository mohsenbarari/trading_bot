"""add_limitation_counters

Revision ID: add_limit_counters
Revises: d1235dbb1628
Create Date: 2025-12-11 09:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'add_limit_counters'
down_revision: Union[str, None] = 'd1235dbb1628'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add counter columns to users table
    op.add_column('users', sa.Column('trades_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('commodities_traded_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('users', sa.Column('channel_messages_count', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'channel_messages_count')
    op.drop_column('users', 'commodities_traded_count')
    op.drop_column('users', 'trades_count')
