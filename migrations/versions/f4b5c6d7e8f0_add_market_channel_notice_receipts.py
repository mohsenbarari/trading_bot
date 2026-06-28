"""add market channel notice receipts

Revision ID: f4b5c6d7e8f0
Revises: f3a4b5c6d7e9
Create Date: 2026-06-28 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4b5c6d7e8f0"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "market_channel_notice_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("transition", sa.String(length=16), nullable=False),
        sa.Column("transition_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notice_text", sa.String(length=240), nullable=False),
        sa.Column("channel_id", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="ux_market_channel_notice_receipts_dedupe_key"),
    )
    op.create_index(op.f("ix_market_channel_notice_receipts_id"), "market_channel_notice_receipts", ["id"], unique=False)
    op.create_index(
        "ix_market_channel_notice_receipts_transition",
        "market_channel_notice_receipts",
        ["transition", "transition_at"],
        unique=False,
    )
    op.create_index(
        "ix_market_channel_notice_receipts_status",
        "market_channel_notice_receipts",
        ["status", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_market_channel_notice_receipts_status", table_name="market_channel_notice_receipts")
    op.drop_index("ix_market_channel_notice_receipts_transition", table_name="market_channel_notice_receipts")
    op.drop_index(op.f("ix_market_channel_notice_receipts_id"), table_name="market_channel_notice_receipts")
    op.drop_table("market_channel_notice_receipts")
