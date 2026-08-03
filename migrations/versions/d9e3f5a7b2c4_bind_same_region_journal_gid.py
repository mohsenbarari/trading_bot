"""Bind opaque journal prepares to their planned PostgreSQL 2PC GID.

Revision ID: d9e3f5a7b2c4
Revises: c8d2e9f4a6b1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d9e3f5a7b2c4"
down_revision = "c8d2e9f4a6b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable preserves any journal evidence created by the first receiver
    # release.  The v2 coordinator refuses to use a row without this binding.
    op.add_column(
        "dr_same_region_journal",
        sa.Column("local_transaction_gid", sa.String(192), nullable=True),
    )
    op.create_index(
        "ux_dr_same_region_journal_local_transaction_gid",
        "dr_same_region_journal",
        ["local_transaction_gid"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "d9e3f5a7b2c4 is forward-only; preserve journal/GID recovery evidence"
    )
