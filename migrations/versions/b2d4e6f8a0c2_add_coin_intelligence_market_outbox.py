"""add durable product market-intelligence outbox

Revision ID: b2d4e6f8a0c2
Revises: a274f5a6b8c9
Create Date: 2026-08-04 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d4e6f8a0c2"
down_revision: Union[str, Sequence[str], None] = "a274f5a6b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVENT_KINDS = (
    "OFFER_OPENED",
    "OFFER_PARTIAL",
    "OFFER_COMPLETED",
    "OFFER_CANCELLED",
    "OFFER_EXPIRED",
    "TRADE_COMPLETED",
)
STATUSES = ("PENDING", "PROCESSING", "COMPLETE", "FAILED")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "coin_intelligence_market_outbox",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("event_kind", sa.String(length=32), nullable=False),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "available_at_utc",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=96), nullable=True),
        sa.Column("completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "model_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            f"event_kind IN ({_quoted(EVENT_KINDS)})",
            name="ck_coin_intelligence_market_outbox_event_kind",
        ),
        sa.CheckConstraint(
            f"status IN ({_quoted(STATUSES)})",
            name="ck_coin_intelligence_market_outbox_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_coin_intelligence_market_outbox_attempts",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_coin_intelligence_market_outbox_idempotency_key",
        ),
    )
    op.create_index(
        "ix_coin_intelligence_market_outbox_claim",
        "coin_intelligence_market_outbox",
        ["status", "available_at_utc", "created_at"],
    )
    op.create_index(
        "ix_coin_intelligence_market_outbox_subject",
        "coin_intelligence_market_outbox",
        ["subject_kind", "subject_id", "occurred_at_utc"],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM coin_intelligence_market_outbox
                ) THEN
                    RAISE EXCEPTION
                        'coin intelligence market outbox must be drained or archived before downgrade';
                END IF;
            END
            $$
            """
        )
    )
    op.drop_index(
        "ix_coin_intelligence_market_outbox_subject",
        table_name="coin_intelligence_market_outbox",
    )
    op.drop_index(
        "ix_coin_intelligence_market_outbox_claim",
        table_name="coin_intelligence_market_outbox",
    )
    op.drop_table("coin_intelligence_market_outbox")
