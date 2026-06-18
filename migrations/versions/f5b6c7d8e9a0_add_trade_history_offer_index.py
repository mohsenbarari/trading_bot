"""add trade history offer index

Revision ID: f5b6c7d8e9a0
Revises: f4a5b6c7d8e9
Create Date: 2026-06-18 20:15:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f5b6c7d8e9a0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_trades_completed_offer_history
            ON trades (offer_id, created_at DESC)
            WHERE status = 'COMPLETED' AND offer_id IS NOT NULL
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_trades_completed_offer_history")
