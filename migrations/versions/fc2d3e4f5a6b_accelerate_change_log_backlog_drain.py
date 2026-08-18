"""accelerate change log backlog drain

Revision ID: fc2d3e4f5a6b
Revises: fb1c2d3e4f5a
Create Date: 2026-08-18 11:15:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "fc2d3e4f5a6b"
down_revision: Union[str, Sequence[str], None] = "fb1c2d3e4f5a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX_NAME = "idx_change_log_unsynced_aggregate_order"


def upgrade() -> None:
    # The sync worker's aggregate-order fence probes only unsynced rows.  A
    # concurrent partial index keeps that hot query independent of the full
    # historical change_log size without blocking live writes during deploy.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
            f"{_INDEX_NAME} ON change_log (table_name, record_id, id) "
            "WHERE synced = false"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX_NAME}")
