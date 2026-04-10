"""add video and location to messagetype enum

Revision ID: e1a2b3c4d5f6
Revises: b5f8a2c3d4e6
Create Date: 2026-04-10 12:00:00.000000

"""
from alembic import op

revision = 'e1a2b3c4d5f6'
down_revision = 'b5f8a2c3d4e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'video'")
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'location'")


def downgrade() -> None:
    pass
