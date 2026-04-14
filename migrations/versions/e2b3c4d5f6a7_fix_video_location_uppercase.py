"""fix video location uppercase

Revision ID: e2b3c4d5f6a7
Revises: e1a2b3c4d5f6
Create Date: 2026-04-14 17:30:00.000000

"""
from alembic import op

revision = 'e2b3c4d5f6a7'
down_revision = 'e1a2b3c4d5f6'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add uppercase values as SQLAlchemy natively uses `.name` by default
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'VIDEO'")
    op.execute("ALTER TYPE messagetype ADD VALUE IF NOT EXISTS 'LOCATION'")

def downgrade() -> None:
    pass
