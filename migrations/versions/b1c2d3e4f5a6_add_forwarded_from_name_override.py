"""add forwarded source name override

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4, e2b3c4d5f6a7, e7f8a9b0c1d2, f4c3b2a1d0e9
Create Date: 2026-06-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b1c2d3e4f5a6"
down_revision = ("a9b8c7d6e5f4", "e2b3c4d5f6a7", "e7f8a9b0c1d2", "f4c3b2a1d0e9")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("forwarded_from_name_override", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("messages", "forwarded_from_name_override")
