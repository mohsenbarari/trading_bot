"""make_address_not_nullable

Revision ID: address_not_null
Revises: add_address_trades
Create Date: 2025-12-13 11:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'address_not_null'
down_revision: Union[str, None] = 'add_address_trades'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Change address column to NOT NULL
    op.alter_column('users', 'address',
                    existing_type=sa.Text(),
                    nullable=False)


def downgrade() -> None:
    op.alter_column('users', 'address',
                    existing_type=sa.Text(),
                    nullable=True)
