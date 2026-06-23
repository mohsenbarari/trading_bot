"""add telegram link tokens

Revision ID: f1a2b3c4d5e8
Revises: e0f1a2b3c4d7
Create Date: 2026-06-23 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f1a2b3c4d5e8"
down_revision = "e0f1a2b3c4d7"
branch_labels = None
depends_on = None


telegram_link_token_status = postgresql.ENUM(
    "pending",
    "used",
    "revoked",
    "expired",
    name="telegramlinktokenstatus",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'telegramlinktokenstatus') THEN
                CREATE TYPE telegramlinktokenstatus AS ENUM ('pending', 'used', 'revoked', 'expired');
            END IF;
        END
        $$;
        """
    )
    op.create_table(
        "telegram_link_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("status", telegram_link_token_status, nullable=False),
        sa.Column("issued_by_server", sa.String(length=16), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(op.f("ix_telegram_link_tokens_id"), "telegram_link_tokens", ["id"], unique=False)
    op.create_index(op.f("ix_telegram_link_tokens_token_hash"), "telegram_link_tokens", ["token_hash"], unique=True)
    op.create_index(op.f("ix_telegram_link_tokens_user_id"), "telegram_link_tokens", ["user_id"], unique=False)
    op.create_index("ix_telegram_link_tokens_user_status", "telegram_link_tokens", ["user_id", "status"], unique=False)
    op.create_index("ix_telegram_link_tokens_expires_at", "telegram_link_tokens", ["expires_at"], unique=False)
    op.create_index(op.f("ix_telegram_link_tokens_status"), "telegram_link_tokens", ["status"], unique=False)
    op.create_index(op.f("ix_telegram_link_tokens_used_telegram_id"), "telegram_link_tokens", ["used_telegram_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_telegram_link_tokens_used_telegram_id"), table_name="telegram_link_tokens")
    op.drop_index(op.f("ix_telegram_link_tokens_status"), table_name="telegram_link_tokens")
    op.drop_index("ix_telegram_link_tokens_expires_at", table_name="telegram_link_tokens")
    op.drop_index("ix_telegram_link_tokens_user_status", table_name="telegram_link_tokens")
    op.drop_index(op.f("ix_telegram_link_tokens_user_id"), table_name="telegram_link_tokens")
    op.drop_index(op.f("ix_telegram_link_tokens_token_hash"), table_name="telegram_link_tokens")
    op.drop_index(op.f("ix_telegram_link_tokens_id"), table_name="telegram_link_tokens")
    op.drop_table("telegram_link_tokens")
    op.execute("DROP TYPE IF EXISTS telegramlinktokenstatus")
