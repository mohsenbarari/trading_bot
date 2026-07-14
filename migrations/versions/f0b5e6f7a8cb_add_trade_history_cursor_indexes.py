"""add trade history cursor indexes

Revision ID: f0b5e6f7a8cb
Revises: e0b5e6f7a8ca
Create Date: 2026-07-14 00:00:00.000000
"""

from alembic import op


revision = "f0b5e6f7a8cb"
down_revision = "e0b5e6f7a8ca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_trades_offer_user_history_cursor
            ON trades (offer_user_id, created_at DESC, id DESC)
            """
        )
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_trades_responder_history_cursor
            ON trades (responder_user_id, created_at DESC, id DESC)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_trades_responder_history_cursor")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_trades_offer_user_history_cursor")
