"""add durable Telegram bot and gateway runtime gates

Revision ID: fd29c0e1f3a4
Revises: fc18b9d0e2f3
Create Date: 2026-07-19 01:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fd29c0e1f3a4"
down_revision: Union[str, Sequence[str], None] = "fc18b9d0e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_delivery_runtime_gates",
        sa.Column("gate_key", sa.String(length=192), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("bot_identity", sa.String(length=128), nullable=True),
        sa.Column("state", sa.String(length=32), server_default=sa.text("'active'"), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason_code", sa.String(length=160), nullable=True),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("retry_after_seconds", sa.Integer(), nullable=True),
        sa.Column("evidence_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("requested_by_hash", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("attempt_history", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("preflight_evidence", sa.JSON(), nullable=True),
        sa.Column(
            "resumed_job_ids",
            sa.JSON(),
            server_default=sa.text("'[]'::json"),
            nullable=False,
        ),
        sa.Column("last_error_class", sa.String(length=160), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("resume_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("database_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redis_applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "version >= 0 AND attempt_count >= 0",
            name="ck_telegram_delivery_runtime_gates_counters",
        ),
        sa.CheckConstraint(
            "evidence_hash IS NULL OR length(evidence_hash) = 64",
            name="ck_telegram_delivery_runtime_gates_evidence_hash",
        ),
        sa.CheckConstraint(
            "((scope = 'bot' AND bot_identity IN ('primary', 'channel_editor') "
            "AND gate_key = 'bot:' || bot_identity) OR "
            "(scope = 'gateway' AND bot_identity IS NULL "
            "AND gate_key = 'gateway:telegram'))",
            name="ck_telegram_delivery_runtime_gates_identity",
        ),
        sa.CheckConstraint(
            "requested_by_hash IS NULL OR length(requested_by_hash) = 64",
            name="ck_telegram_delivery_runtime_gates_requested_by_hash",
        ),
        sa.CheckConstraint(
            "scope IN ('bot', 'gateway')",
            name="ck_telegram_delivery_runtime_gates_scope",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'cooldown', 'blocked', 'resume_requested', "
            "'database_applied')",
            name="ck_telegram_delivery_runtime_gates_state",
        ),
        sa.PrimaryKeyConstraint("gate_key"),
    )


def downgrade() -> None:
    op.drop_table("telegram_delivery_runtime_gates")
