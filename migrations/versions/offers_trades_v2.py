"""add_offers_update_trades

Revision ID: offers_trades_v2
Revises: address_not_null
Create Date: 2025-12-13 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'offers_trades_v2'
down_revision: Union[str, None] = 'address_not_null'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Drop old trades table (if exists with old schema)
    op.drop_index('ix_trades_created_at', table_name='trades', if_exists=True)
    op.drop_index('ix_trades_user_id', table_name='trades', if_exists=True)
    op.drop_index('ix_trades_id', table_name='trades', if_exists=True)
    op.drop_table('trades')
    
    # Drop old tradetype enum if exists
    op.execute("DROP TYPE IF EXISTS tradetype CASCADE")
    
    # 2. Create offers table (لفظ)
    op.create_table(
        'offers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('offer_type', sa.Enum('BUY', 'SELL', name='offertype', create_type=True), nullable=False),
        sa.Column('commodity_id', sa.Integer(), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Enum('ACTIVE', 'COMPLETED', 'CANCELLED', 'EXPIRED', name='offerstatus', create_type=True), nullable=False, server_default='ACTIVE'),
        sa.Column('channel_message_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['commodity_id'], ['commodities.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_offers_id', 'offers', ['id'], unique=False)
    op.create_index('ix_offers_user_id', 'offers', ['user_id'], unique=False)
    op.create_index('ix_offers_status', 'offers', ['status'], unique=False)
    op.create_index('ix_offers_created_at', 'offers', ['created_at'], unique=False)
    
    # 3. Create new trades table (معامله)
    op.create_table(
        'trades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('offer_id', sa.Integer(), nullable=False),
        sa.Column('offer_user_id', sa.Integer(), nullable=False),
        sa.Column('responder_user_id', sa.Integer(), nullable=False),
        sa.Column('commodity_id', sa.Integer(), nullable=False),
        sa.Column('trade_type', sa.Enum('BUY', 'SELL', name='tradetype', create_type=True), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('price', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Enum('PENDING', 'CONFIRMED', 'COMPLETED', 'CANCELLED', name='tradestatus', create_type=True), nullable=False, server_default='PENDING'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['offer_id'], ['offers.id'], ),
        sa.ForeignKeyConstraint(['offer_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['responder_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['commodity_id'], ['commodities.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_trades_id', 'trades', ['id'], unique=False)
    op.create_index('ix_trades_offer_user_id', 'trades', ['offer_user_id'], unique=False)
    op.create_index('ix_trades_responder_user_id', 'trades', ['responder_user_id'], unique=False)
    op.create_index('ix_trades_status', 'trades', ['status'], unique=False)
    op.create_index('ix_trades_created_at', 'trades', ['created_at'], unique=False)


def downgrade() -> None:
    # Drop new tables
    op.drop_index('ix_trades_created_at', table_name='trades')
    op.drop_index('ix_trades_status', table_name='trades')
    op.drop_index('ix_trades_responder_user_id', table_name='trades')
    op.drop_index('ix_trades_offer_user_id', table_name='trades')
    op.drop_index('ix_trades_id', table_name='trades')
    op.drop_table('trades')
    
    op.drop_index('ix_offers_created_at', table_name='offers')
    op.drop_index('ix_offers_status', table_name='offers')
    op.drop_index('ix_offers_user_id', table_name='offers')
    op.drop_index('ix_offers_id', table_name='offers')
    op.drop_table('offers')
