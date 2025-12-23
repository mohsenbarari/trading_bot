"""add_check_constraints_for_positive_values

Revision ID: 6e7ad5733d5a
Revises: d3f557edf163
Create Date: 2025-12-23 10:38:10.459242

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e7ad5733d5a'
down_revision: Union[str, None] = 'd3f557edf163'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # اضافه کردن CheckConstraints برای جلوگیری از اعداد منفی
    
    # Offers table
    op.create_check_constraint(
        'ck_offers_quantity_positive',
        'offers',
        'quantity > 0'
    )
    op.create_check_constraint(
        'ck_offers_price_positive',
        'offers',
        'price > 0'
    )
    op.create_check_constraint(
        'ck_offers_remaining_nonnegative',
        'offers',
        'remaining_quantity >= 0'
    )
    
    # Trades table
    op.create_check_constraint(
        'ck_trades_quantity_positive',
        'trades',
        'quantity > 0'
    )
    op.create_check_constraint(
        'ck_trades_price_positive',
        'trades',
        'price > 0'
    )


def downgrade() -> None:
    # حذف CheckConstraints
    op.drop_constraint('ck_trades_price_positive', 'trades', type_='check')
    op.drop_constraint('ck_trades_quantity_positive', 'trades', type_='check')
    op.drop_constraint('ck_offers_remaining_nonnegative', 'offers', type_='check')
    op.drop_constraint('ck_offers_price_positive', 'offers', type_='check')
    op.drop_constraint('ck_offers_quantity_positive', 'offers', type_='check')

