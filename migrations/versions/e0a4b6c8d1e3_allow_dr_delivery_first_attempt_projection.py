"""Allow the retained DR delivery first-attempt timestamp in projections.

Revision ID: e0a4b6c8d1e3
Revises: d9e3f5a7b2c4
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e0a4b6c8d1e3"
down_revision = "d9e3f5a7b2c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Repair the policy delta introduced after the policy reconciliation.

    ``a875b6c7d9e0`` added ``first_attempt_at`` after the integrated
    projection allowlist had been populated.  Delivery workers update this
    timestamp under the database projection fence, so omitting it makes every
    claim fail closed and restart the worker.  Grant only that one local DR
    bookkeeping field; do not rebuild or broaden the allowlist.
    """

    op.execute(
        sa.text(
            "INSERT INTO dr_projection_field_allowlist (table_name, column_name) "
            "VALUES (:table_name, :column_name) "
            "ON CONFLICT (table_name, column_name) DO NOTHING"
        ).bindparams(
            table_name="dr_event_deliveries",
            column_name="first_attempt_at",
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "e0a4b6c8d1e3 is forward-only; preserve the repaired projection policy"
    )
