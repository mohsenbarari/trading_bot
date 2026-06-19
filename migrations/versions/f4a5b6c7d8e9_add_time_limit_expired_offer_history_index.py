"""add time-limit expired offer history index

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-06-17 20:10:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "f4a5b6c7d8e9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_offers_time_limit_expired_history
            ON offers ((COALESCE(expired_at, updated_at, created_at)) DESC, created_at DESC)
            WHERE status = 'EXPIRED' AND expire_reason = 'time_limit'
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_offers_time_limit_expired_history")
