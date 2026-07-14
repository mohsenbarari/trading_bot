"""add active offer cursor index

Revision ID: e0b5e6f7a8ca
Revises: d0b5e6f7a8c9
Create Date: 2026-07-14 00:00:00.000000
"""

from alembic import op


revision = "e0b5e6f7a8ca"
down_revision = "d0b5e6f7a8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_offers_active_created_id
            ON offers (created_at DESC, id DESC)
            WHERE status = 'ACTIVE'
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_offers_active_created_id")
