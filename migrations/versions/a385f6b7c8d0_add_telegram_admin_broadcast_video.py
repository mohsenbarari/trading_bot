"""add telegram admin broadcast video media contract

Revision ID: a385f6b7c8d0
Revises: ff5a6b7c8d9e
Create Date: 2026-08-23 12:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a385f6b7c8d0"
down_revision: Union[str, Sequence[str], None] = "ff5a6b7c8d9e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


content_kind = postgresql.ENUM(
    "text",
    "video",
    name="telegramadminbroadcastcontentkind",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    content_kind.create(bind, checkfirst=True)

    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column(
            "content_kind",
            content_kind,
            nullable=True,
        ),
    )
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("telegram_media_file_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("telegram_media_file_unique_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("media_duration_seconds", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("media_width", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("media_height", sa.Integer(), nullable=True),
    )
    op.add_column(
        "telegram_admin_broadcasts",
        sa.Column("media_file_size", sa.BigInteger(), nullable=True),
    )

    op.execute(
        sa.text(
            "UPDATE telegram_admin_broadcasts "
            "SET content_kind = 'text' "
            "WHERE content_kind IS NULL"
        )
    )
    op.alter_column(
        "telegram_admin_broadcasts",
        "content_kind",
        existing_type=content_kind,
        nullable=False,
        server_default=sa.text("'text'::telegramadminbroadcastcontentkind"),
    )
    op.create_check_constraint(
        "ck_telegram_admin_broadcasts_content_kind_media",
        "telegram_admin_broadcasts",
        "("
        "content_kind = 'text' "
        "AND telegram_media_file_id IS NULL "
        "AND telegram_media_file_unique_id IS NULL"
        ") OR ("
        "content_kind = 'video' "
        "AND telegram_media_file_id IS NOT NULL "
        "AND btrim(telegram_media_file_id) <> '' "
        "AND telegram_media_file_unique_id IS NOT NULL "
        "AND btrim(telegram_media_file_unique_id) <> ''"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_telegram_admin_broadcasts_content_kind_media",
        "telegram_admin_broadcasts",
        type_="check",
    )
    op.drop_column("telegram_admin_broadcasts", "media_file_size")
    op.drop_column("telegram_admin_broadcasts", "media_height")
    op.drop_column("telegram_admin_broadcasts", "media_width")
    op.drop_column("telegram_admin_broadcasts", "media_duration_seconds")
    op.drop_column("telegram_admin_broadcasts", "telegram_media_file_unique_id")
    op.drop_column("telegram_admin_broadcasts", "telegram_media_file_id")
    op.drop_column("telegram_admin_broadcasts", "content_kind")
    content_kind.drop(op.get_bind(), checkfirst=True)
