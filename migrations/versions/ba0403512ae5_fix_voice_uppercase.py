"""fix_voice_uppercase

Revision ID: ba0403512ae5
Revises: 352da8093009
Create Date: 2026-04-18 18:02:55.270495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba0403512ae5'
down_revision: Union[str, None] = '352da8093009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add uppercase VOICE to messagetype since SQLAlchemy uses the member name
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'VOICE'")


def downgrade() -> None:
    pass
