"""add offer idempotency fingerprints

Revision ID: f1b6e7f8a9dc
Revises: f0b5e6f7a8cb
Create Date: 2026-07-14 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


revision = "f1b6e7f8a9dc"
down_revision = "f0b5e6f7a8cb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("idempotency_fingerprint_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "offers",
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("offers", "idempotency_fingerprint")
    op.drop_column("offers", "idempotency_fingerprint_version")
