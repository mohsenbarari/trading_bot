"""add WebApp writer witness lease contract

Revision ID: d2e7f8a9b0c1
Revises: d1c6e7f8a9b0
Create Date: 2026-07-14 23:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e7f8a9b0c1"
down_revision: Union[str, Sequence[str], None] = "d1c6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BOOTSTRAP_WITNESS_TRANSITION_ID = "00000000-0000-0000-0000-000000000002"


def upgrade() -> None:
    op.add_column(
        "webapp_writer_state",
        sa.Column("witness_lease_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webapp_writer_state",
        sa.Column("witness_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "webapp_writer_state",
        sa.Column("witness_proof_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webapp_writer_state",
        sa.Column("witness_transition_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "webapp_writer_transitions",
        sa.Column("witness_proof_hash", sa.String(length=64), nullable=True),
    )

    op.drop_constraint(
        "ck_webapp_writer_transitions_action",
        "webapp_writer_transitions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_webapp_writer_transitions_action",
        "webapp_writer_transitions",
        "action IN ('bootstrap', 'fence', 'activate', 'approve', 'handoff', 'lease_refresh')",
    )

    op.create_table(
        "webapp_writer_witness_state",
        sa.Column("authority", sa.String(length=16), nullable=False),
        sa.Column("holder_site", sa.String(length=16), nullable=True),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("lease_id", sa.String(length=64), nullable=True),
        sa.Column("lease_status", sa.String(length=16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transition_id", sa.String(length=64), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("authority = 'webapp'", name="ck_webapp_writer_witness_authority"),
        sa.CheckConstraint("writer_epoch >= 0", name="ck_webapp_writer_witness_epoch"),
        sa.CheckConstraint(
            "holder_site IS NULL OR holder_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_webapp_writer_witness_holder",
        ),
        sa.CheckConstraint(
            "lease_status IN ('vacant', 'leased', 'draining')",
            name="ck_webapp_writer_witness_status",
        ),
        sa.CheckConstraint(
            "(lease_status = 'vacant' AND holder_site IS NULL AND lease_id IS NULL "
            "AND issued_at IS NULL AND expires_at IS NULL) OR "
            "(lease_status <> 'vacant' AND holder_site IS NOT NULL AND lease_id IS NOT NULL "
            "AND issued_at IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_webapp_writer_witness_consistency",
        ),
        sa.PrimaryKeyConstraint("authority"),
    )
    op.create_table(
        "webapp_writer_witness_receipts",
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("transition_id", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action IN ('acquire', 'renew', 'drain')",
            name="ck_webapp_writer_witness_receipt_action",
        ),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index(
        "ix_webapp_writer_witness_receipts_created_at",
        "webapp_writer_witness_receipts",
        ["created_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO webapp_writer_witness_state (
                authority, holder_site, writer_epoch, lease_id, lease_status,
                issued_at, expires_at, transition_id, updated_by, reason
            ) VALUES (
                'webapp', NULL, 0, NULL, 'vacant', NULL, NULL,
                :transition_id, 'migration',
                'witness bootstrap: no lease has been granted'
            )
            """
        ).bindparams(transition_id=BOOTSTRAP_WITNESS_TRANSITION_ID)
    )


def downgrade() -> None:
    op.drop_index(
        "ix_webapp_writer_witness_receipts_created_at",
        table_name="webapp_writer_witness_receipts",
    )
    op.drop_table("webapp_writer_witness_receipts")
    op.drop_table("webapp_writer_witness_state")

    op.drop_constraint(
        "ck_webapp_writer_transitions_action",
        "webapp_writer_transitions",
        type_="check",
    )
    # lease_refresh is local control-plane audit only and the previous schema
    # cannot represent it. Remove those rows before restoring the old check.
    op.execute(
        sa.text("DELETE FROM webapp_writer_transitions WHERE action = 'lease_refresh'")
    )
    op.create_check_constraint(
        "ck_webapp_writer_transitions_action",
        "webapp_writer_transitions",
        "action IN ('bootstrap', 'fence', 'activate', 'approve', 'handoff')",
    )

    op.drop_column("webapp_writer_state", "witness_transition_id")
    op.drop_column("webapp_writer_state", "witness_proof_hash")
    op.drop_column("webapp_writer_state", "witness_lease_expires_at")
    op.drop_column("webapp_writer_state", "witness_lease_id")
    op.drop_column("webapp_writer_transitions", "witness_proof_hash")
