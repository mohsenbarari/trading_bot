"""add offer expire reason

Revision ID: e1f2a3b4c5d6
Revises: b6c7d8e9f0a1
Create Date: 2026-05-22 08:35:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("expire_reason", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "expire_reason")