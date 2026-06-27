"""add sync apply watermarks

Revision ID: f3a4b5c6d7e9
Revises: f2a3b4c5d6e9
Create Date: 2026-06-27 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e9"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_apply_watermarks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_server", sa.String(length=16), nullable=False),
        sa.Column("aggregate_table", sa.String(length=64), nullable=False),
        sa.Column("aggregate_key", sa.String(length=255), nullable=False),
        sa.Column("last_source_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_payload_hash", sa.String(length=64), nullable=False),
        sa.Column("last_operation", sa.String(length=10), nullable=False),
        sa.Column("last_record_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_server",
            "aggregate_table",
            "aggregate_key",
            name="ux_sync_apply_watermarks_source_aggregate",
        ),
    )
    op.create_index(op.f("ix_sync_apply_watermarks_id"), "sync_apply_watermarks", ["id"], unique=False)
    op.create_index(
        "ix_sync_apply_watermarks_source_table_sequence",
        "sync_apply_watermarks",
        ["source_server", "aggregate_table", "last_source_sequence"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_sync_apply_watermarks_source_table_sequence", table_name="sync_apply_watermarks")
    op.drop_index(op.f("ix_sync_apply_watermarks_id"), table_name="sync_apply_watermarks")
    op.drop_table("sync_apply_watermarks")
