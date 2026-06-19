"""add offer public id

Revision ID: a6b7c8d9e0f1
Revises: f5b6c7d8e9a0
Create Date: 2026-06-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a6b7c8d9e0f1"
down_revision = "f5b6c7d8e9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("offer_public_id", sa.String(length=40), nullable=True))
    op.execute(
        """
        UPDATE offers
        SET offer_public_id = 'ofr_' || substr(md5(random()::text || clock_timestamp()::text || id::text), 1, 24)
        WHERE offer_public_id IS NULL
        """
    )
    op.alter_column("offers", "offer_public_id", existing_type=sa.String(length=40), nullable=False)
    op.create_index(op.f("ix_offers_offer_public_id"), "offers", ["offer_public_id"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_offers_offer_public_id"), table_name="offers")
    op.drop_column("offers", "offer_public_id")
