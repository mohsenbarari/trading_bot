"""remove unique from invitation mobile and account_name

Revision ID: a1b2c3d4e5f6
Revises: 0ed74190b05a
Create Date: 2026-02-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: str = '6a5704ca78cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop unique constraints on account_name and mobile_number
    # These prevent re-inviting the same user after invitation expiry
    op.drop_index('ix_invitations_account_name', table_name='invitations')
    op.drop_index('ix_invitations_mobile_number', table_name='invitations')
    op.create_index('ix_invitations_account_name', 'invitations', ['account_name'], unique=False)
    op.create_index('ix_invitations_mobile_number', 'invitations', ['mobile_number'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_invitations_account_name', table_name='invitations')
    op.drop_index('ix_invitations_mobile_number', table_name='invitations')
    op.create_index('ix_invitations_account_name', 'invitations', ['account_name'], unique=True)
    op.create_index('ix_invitations_mobile_number', 'invitations', ['mobile_number'], unique=True)
