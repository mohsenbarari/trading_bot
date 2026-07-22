"""Retain the first DR delivery attempt for trustworthy latency evidence.

Revision ID: a875b6c7d9e0
Revises: f764a5b6c8d9
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "a875b6c7d9e0"
down_revision = "f764a5b6c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dr_event_deliveries",
        sa.Column("first_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE dr_event_deliveries "
        "SET first_attempt_at=last_attempt_at "
        "WHERE first_attempt_at IS NULL AND last_attempt_at IS NOT NULL"
    )
    op.create_check_constraint(
        "ck_dr_event_deliveries_first_attempt_order",
        "dr_event_deliveries",
        "first_attempt_at IS NULL OR ("
        "last_attempt_at IS NOT NULL AND first_attempt_at <= last_attempt_at)",
    )


def downgrade() -> None:
    raise RuntimeError(
        "a875b6c7d9e0 is a forward-only evidence migration; use the reviewed restore/forward-rollback runbook"
    )
