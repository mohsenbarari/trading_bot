"""add_notes_to_offer

Revision ID: 7501fae7c9a4
Revises: 133ae92d599b
Create Date: 2025-12-16 11:27:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7501fae7c9a4'
down_revision: Union[str, None] = '133ae92d599b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('offers', sa.Column('notes', sa.String(length=200), nullable=True))


def downgrade() -> None:
    op.drop_column('offers', 'notes')
