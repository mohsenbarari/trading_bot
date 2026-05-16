"""add offer price warning flags

Revision ID: d7c8e9f0a1b2
Revises: c1d2e3f4a5b6
Create Date: 2026-05-16 12:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7c8e9f0a1b2'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'offers',
        sa.Column('exclude_from_competitive_price', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column(
        'offers',
        sa.Column('price_warning_type', sa.String(length=64), nullable=True),
    )
    op.create_index(
        op.f('ix_offers_exclude_from_competitive_price'),
        'offers',
        ['exclude_from_competitive_price'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_offers_exclude_from_competitive_price'), table_name='offers')
    op.drop_column('offers', 'price_warning_type')
    op.drop_column('offers', 'exclude_from_competitive_price')