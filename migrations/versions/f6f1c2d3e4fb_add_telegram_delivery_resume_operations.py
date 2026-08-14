"""add durable Telegram delivery resume operations

Revision ID: f6f1c2d3e4fb
Revises: f5e0b1c2d3ea
Create Date: 2026-07-18 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6f1c2d3e4fb"
down_revision: Union[str, Sequence[str], None] = "f5e0b1c2d3ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_delivery_resume_operations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column(
            "scope",
            sa.String(length=32),
            server_default=sa.text("'channel_destination'"),
            nullable=False,
        ),
        sa.Column("destination_key", sa.String(length=256), nullable=False),
        sa.Column("bot_identities", sa.JSON(), nullable=False),
        sa.Column("pause_job_ids", sa.JSON(), nullable=False),
        sa.Column("pause_evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=128), nullable=False),
        sa.Column(
            "attempt_history",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("preflight_evidence", sa.JSON(), nullable=True),
        sa.Column(
            "state",
            sa.String(length=32),
            server_default=sa.text("'requested'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("failure_class", sa.String(length=160), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preflight_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("db_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redis_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_telegram_delivery_resume_operations_attempt_count",
        ),
        sa.CheckConstraint(
            "length(pause_evidence_hash) = 64",
            name="ck_telegram_delivery_resume_operations_evidence_hash",
        ),
        sa.CheckConstraint(
            "((state = 'requested' AND db_applied_at IS NULL AND "
            "redis_applied_at IS NULL AND completed_at IS NULL) OR "
            "(state = 'failed' AND db_applied_at IS NULL AND "
            "redis_applied_at IS NULL AND completed_at IS NULL AND "
            "failure_class IS NOT NULL) OR "
            "(state = 'database_applied' AND db_applied_at IS NOT NULL AND "
            "redis_applied_at IS NULL AND completed_at IS NULL) OR "
            "(state = 'redis_applied' AND db_applied_at IS NOT NULL AND "
            "redis_applied_at IS NOT NULL AND completed_at IS NULL) OR "
            "(state = 'completed' AND db_applied_at IS NOT NULL AND "
            "redis_applied_at IS NOT NULL AND completed_at IS NOT NULL))",
            name="ck_telegram_delivery_resume_operations_phase_timestamps",
        ),
        sa.CheckConstraint(
            "length(request_id) BETWEEN 16 AND 128",
            name="ck_telegram_delivery_resume_operations_request_id",
        ),
        sa.CheckConstraint(
            "length(requested_by) BETWEEN 1 AND 128",
            name="ck_telegram_delivery_resume_operations_requested_by",
        ),
        sa.CheckConstraint(
            "scope = 'channel_destination'",
            name="ck_telegram_delivery_resume_operations_scope",
        ),
        sa.CheckConstraint(
            "state IN ('requested', 'database_applied', 'redis_applied', "
            "'completed', 'failed')",
            name="ck_telegram_delivery_resume_operations_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id",
            name="ux_telegram_delivery_resume_operations_request_id",
        ),
    )
    op.create_index(
        "ix_telegram_delivery_resume_operations_state",
        "telegram_delivery_resume_operations",
        ["state", "updated_at", "id"],
        unique=False,
    )
    op.create_index(
        "ux_telegram_delivery_resume_active_destination",
        "telegram_delivery_resume_operations",
        ["destination_key"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('requested', 'database_applied', 'redis_applied')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_telegram_delivery_resume_active_destination",
        table_name="telegram_delivery_resume_operations",
    )
    op.drop_index(
        "ix_telegram_delivery_resume_operations_state",
        table_name="telegram_delivery_resume_operations",
    )
    op.drop_table("telegram_delivery_resume_operations")
