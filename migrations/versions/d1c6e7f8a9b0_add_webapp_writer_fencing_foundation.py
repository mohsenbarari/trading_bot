"""add WebApp writer fencing foundation

Revision ID: d1c6e7f8a9b0
Revises: d0b5e6f7a8c9
Create Date: 2026-07-14 21:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1c6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "d0b5e6f7a8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


BOOTSTRAP_TRANSITION_ID = "00000000-0000-0000-0000-000000000001"


def upgrade() -> None:
    op.create_table(
        "webapp_writer_state",
        sa.Column("authority", sa.String(length=16), nullable=False),
        sa.Column("active_site", sa.String(length=16), nullable=True),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("control_state", sa.String(length=16), nullable=False),
        sa.Column("transition_id", sa.String(length=36), nullable=False),
        sa.Column("readiness_evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("readiness_evidence_id", sa.String(length=64), nullable=True),
        sa.Column("readiness_approved_by", sa.String(length=128), nullable=True),
        sa.Column("readiness_approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("readiness_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("authority = 'webapp'", name="ck_webapp_writer_state_authority"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_webapp_writer_state_epoch_positive"),
        sa.CheckConstraint(
            "active_site IS NULL OR active_site IN ('webapp_fi', 'webapp_ir')",
            name="ck_webapp_writer_state_active_site",
        ),
        sa.CheckConstraint(
            "control_state IN ('active', 'fenced', 'handoff')",
            name="ck_webapp_writer_state_control_state",
        ),
        sa.CheckConstraint(
            "(control_state = 'active' AND active_site IS NOT NULL) OR "
            "(control_state <> 'active' AND active_site IS NULL)",
            name="ck_webapp_writer_state_active_consistency",
        ),
        sa.PrimaryKeyConstraint("authority"),
    )
    op.create_table(
        "webapp_writer_transitions",
        sa.Column("transition_id", sa.String(length=36), nullable=False),
        sa.Column("authority", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("previous_active_site", sa.String(length=16), nullable=True),
        sa.Column("new_active_site", sa.String(length=16), nullable=True),
        sa.Column("previous_epoch", sa.BigInteger(), nullable=False),
        sa.Column("new_epoch", sa.BigInteger(), nullable=False),
        sa.Column("operator", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "action IN ('bootstrap', 'fence', 'activate', 'approve', 'handoff')",
            name="ck_webapp_writer_transitions_action",
        ),
        sa.CheckConstraint(
            "previous_epoch >= 1 AND new_epoch >= previous_epoch",
            name="ck_webapp_writer_transitions_epoch",
        ),
        sa.PrimaryKeyConstraint("transition_id"),
    )
    op.create_index(
        "ix_webapp_writer_transitions_created_at",
        "webapp_writer_transitions",
        ["created_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO webapp_writer_state (
                authority, active_site, writer_epoch, control_state,
                transition_id, updated_by, reason
            ) VALUES (
                'webapp', 'webapp_fi', 1, 'active',
                :transition_id, 'migration',
                'compatibility bootstrap: current WebApp writer remains webapp_fi'
            )
            """
        ).bindparams(transition_id=BOOTSTRAP_TRANSITION_ID)
    )
    op.execute(
        sa.text(
            """
            INSERT INTO webapp_writer_transitions (
                transition_id, authority, action, previous_active_site,
                new_active_site, previous_epoch, new_epoch, operator, reason
            ) VALUES (
                :transition_id, 'webapp', 'bootstrap', NULL,
                'webapp_fi', 1, 1, 'migration',
                'compatibility bootstrap: current WebApp writer remains webapp_fi'
            )
            """
        ).bindparams(transition_id=BOOTSTRAP_TRANSITION_ID)
    )


def downgrade() -> None:
    op.drop_index("ix_webapp_writer_transitions_created_at", table_name="webapp_writer_transitions")
    op.drop_table("webapp_writer_transitions")
    op.drop_table("webapp_writer_state")
