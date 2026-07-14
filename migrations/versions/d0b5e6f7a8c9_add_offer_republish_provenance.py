"""add immutable offer republish provenance

Revision ID: d0b5e6f7a8c9
Revises: c7d8e9f0a1b4, c9a4e7b2d615
Create Date: 2026-07-14 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0b5e6f7a8c9"
down_revision: Union[str, Sequence[str], None] = ("c7d8e9f0a1b4", "c9a4e7b2d615")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "offers",
        sa.Column("republished_from_offer_public_id", sa.String(length=40), nullable=True),
    )
    op.execute(
        """
        UPDATE offers AS replacement
        SET republished_from_offer_public_id = source.offer_public_id
        FROM offers AS source
        WHERE source.republished_offer_public_id = replacement.offer_public_id
          AND replacement.republished_from_offer_public_id IS NULL
        """
    )
    op.create_index(
        op.f("ix_offers_republished_from_offer_public_id"),
        "offers",
        ["republished_from_offer_public_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_offers_republished_from_offer_public_id"), table_name="offers")
    op.drop_column("offers", "republished_from_offer_public_id")
