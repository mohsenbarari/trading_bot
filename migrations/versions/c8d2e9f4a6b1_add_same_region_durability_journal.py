"""Add the opaque two-phase same-region durability journal.

Revision ID: c8d2e9f4a6b1
Revises: b986c7d8e0f1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c8d2e9f4a6b1"
down_revision = "b986c7d8e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The same migration runs on every independent role database to preserve a
    # single Alembic graph.  Only the dedicated Bot-FI journal role is granted
    # access by role provisioning; no WebApp/Bot application role receives it.
    op.create_table(
        "dr_same_region_journal",
        sa.Column("origin_physical_site", sa.String(16), primary_key=True),
        sa.Column("writer_epoch", sa.BigInteger(), primary_key=True),
        sa.Column("transaction_id", sa.String(36), primary_key=True),
        sa.Column("transaction_hash", sa.String(64), nullable=False),
        sa.Column("release_sha", sa.String(64), nullable=False),
        sa.Column("encryption_key_id", sa.String(64), nullable=False),
        sa.Column("event_ids", sa.JSON(), nullable=False),
        sa.Column("event_hashes", sa.JSON(), nullable=False),
        sa.Column("nonce", sa.String(32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("ciphertext_hash", sa.String(64), nullable=False),
        sa.Column("prepare_request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column("prepared_transaction_gid", sa.String(192), nullable=True),
        sa.Column(
            "prepared_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_dr_same_region_journal_epoch"),
        sa.CheckConstraint(
            "state IN ('prepared', 'committed', 'rolled_back')",
            name="ck_dr_same_region_journal_state",
        ),
        sa.CheckConstraint(
            "(state = 'prepared' AND resolved_at IS NULL) OR "
            "(state IN ('committed', 'rolled_back') AND resolved_at IS NOT NULL)",
            name="ck_dr_same_region_journal_resolution_time",
        ),
        sa.CheckConstraint(
            "(state = 'committed' AND prepared_transaction_gid IS NOT NULL) OR "
            "(state <> 'committed')",
            name="ck_dr_same_region_journal_commit_gid",
        ),
    )
    op.create_index(
        "ix_dr_same_region_journal_state_prepared_at",
        "dr_same_region_journal",
        ["state", "prepared_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "c8d2e9f4a6b1 is forward-only; preserve journal evidence and use the reviewed rollback runbook"
    )
