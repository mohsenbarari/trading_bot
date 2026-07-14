"""add offer expiry command receipts and canonical republish lineage

Revision ID: c9a4e7b2d615
Revises: b9e0f1a2c3d4
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c9a4e7b2d615"
down_revision: Union[str, Sequence[str], None] = "b9e0f1a2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("republished_offer_public_id", sa.String(length=40), nullable=True))
    op.execute(
        """
        UPDATE offers AS source
        SET republished_offer_public_id = replacement.offer_public_id
        FROM offers AS replacement
        WHERE source.republished_offer_id = replacement.id
          AND source.republished_offer_public_id IS NULL
        """
    )
    op.create_index(
        op.f("ix_offers_republished_offer_public_id"),
        "offers",
        ["republished_offer_public_id"],
        unique=False,
    )

    op.create_table(
        "offer_expiry_command_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("command_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=192), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("offer_public_id", sa.String(length=40), nullable=False),
        sa.Column("replacement_offer_public_id", sa.String(length=40), nullable=True),
        sa.Column("source_server", sa.String(length=16), nullable=False),
        sa.Column("source_surface", sa.String(length=32), nullable=False),
        sa.Column("expire_reason", sa.String(length=32), nullable=False),
        sa.Column("outcome_code", sa.String(length=64), nullable=True),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(request_hash) = 64", name="ck_offer_expiry_receipts_request_hash"),
        sa.CheckConstraint(
            "((outcome_code IS NULL AND completed_at IS NULL) OR "
            "(outcome_code IS NOT NULL AND completed_at IS NOT NULL))",
            name="ck_offer_expiry_receipts_terminal_atomic",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id", name="ux_offer_expiry_receipts_command_id"),
        sa.UniqueConstraint("idempotency_key", name="ux_offer_expiry_receipts_idempotency_key"),
    )
    op.create_index(op.f("ix_offer_expiry_command_receipts_id"), "offer_expiry_command_receipts", ["id"], unique=False)
    op.create_index(
        op.f("ix_offer_expiry_command_receipts_offer_public_id"),
        "offer_expiry_command_receipts",
        ["offer_public_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_offer_expiry_command_receipts_replacement_offer_public_id"),
        "offer_expiry_command_receipts",
        ["replacement_offer_public_id"],
        unique=False,
    )
    op.create_index("ix_offer_expiry_receipts_completed_at", "offer_expiry_command_receipts", ["completed_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_offer_expiry_receipts_completed_at", table_name="offer_expiry_command_receipts")
    op.drop_index(
        op.f("ix_offer_expiry_command_receipts_replacement_offer_public_id"),
        table_name="offer_expiry_command_receipts",
    )
    op.drop_index(op.f("ix_offer_expiry_command_receipts_offer_public_id"), table_name="offer_expiry_command_receipts")
    op.drop_index(op.f("ix_offer_expiry_command_receipts_id"), table_name="offer_expiry_command_receipts")
    op.drop_table("offer_expiry_command_receipts")
    op.drop_index(op.f("ix_offers_republished_offer_public_id"), table_name="offers")
    op.drop_column("offers", "republished_offer_public_id")
