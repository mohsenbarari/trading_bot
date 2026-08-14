"""add durable Telegram provider outcome inbox

Revision ID: fc18b9d0e2f3
Revises: fb07b8c9d0e1
Create Date: 2026-07-19 00:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fc18b9d0e2f3"
down_revision: Union[str, Sequence[str], None] = "fb07b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_delivery_provider_outcomes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("lease_token", sa.BigInteger(), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("bot_identity", sa.String(length=128), nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("gateway_ok", sa.Boolean(), nullable=False),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("provider_response", sa.JSON(), nullable=True),
        sa.Column("provider_error_class", sa.String(length=120), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("retry_after_seconds", sa.BigInteger(), nullable=True),
        sa.Column("outcome_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "apply_state",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "apply_attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("next_apply_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_apply_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_apply_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "apply_attempt_count >= 0",
            name="ck_telegram_delivery_provider_outcomes_apply_attempts",
        ),
        sa.CheckConstraint(
            "apply_state IN ('pending', 'applied', 'quarantined')",
            name="ck_telegram_delivery_provider_outcomes_apply_state",
        ),
        sa.CheckConstraint(
            "lease_token > 0",
            name="ck_telegram_delivery_provider_outcomes_lease_token",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["telegram_delivery_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "lease_token",
            name="ux_telegram_delivery_provider_outcomes_fence",
        ),
    )
    op.create_index(
        "ix_telegram_delivery_provider_outcomes_job",
        "telegram_delivery_provider_outcomes",
        ["job_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_delivery_provider_outcomes_pending",
        "telegram_delivery_provider_outcomes",
        ["next_apply_at", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("apply_state = 'pending'"),
    )
    op.create_table(
        "telegram_delivery_reconciliation_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("observed_job_state", sa.String(length=48), nullable=False),
        sa.Column("evidence_kind", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_action", sa.String(length=64), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("actor_ref_hash", sa.String(length=64), nullable=True),
        sa.Column("reason_code", sa.String(length=160), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_kind IN ('worker', 'operator')",
            name="ck_telegram_delivery_reconciliation_actor_kind",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["telegram_delivery_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "evidence_hash",
            "decision_action",
            name="ux_telegram_delivery_reconciliation_evidence_identity",
        ),
    )
    op.create_index(
        "ix_telegram_delivery_reconciliation_evidence_job",
        "telegram_delivery_reconciliation_evidence",
        ["job_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_delivery_reconciliation_evidence_job",
        table_name="telegram_delivery_reconciliation_evidence",
    )
    op.drop_table("telegram_delivery_reconciliation_evidence")
    op.drop_index(
        "ix_telegram_delivery_provider_outcomes_pending",
        table_name="telegram_delivery_provider_outcomes",
    )
    op.drop_index(
        "ix_telegram_delivery_provider_outcomes_job",
        table_name="telegram_delivery_provider_outcomes",
    )
    op.drop_table("telegram_delivery_provider_outcomes")
