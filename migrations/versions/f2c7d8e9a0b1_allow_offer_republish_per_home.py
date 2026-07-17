"""allow one offer republish per home server

Revision ID: f2c7d8e9a0b1
Revises: f1b6e7f8a9dc
Create Date: 2026-07-17 00:00:00.000000
"""

from alembic import op


revision = "f2c7d8e9a0b1"
down_revision = "f1b6e7f8a9dc"
branch_labels = None
depends_on = None


OLD_INDEX = "ix_offers_republished_from_offer_public_id"
PER_HOME_INDEX = "uq_offers_republished_from_offer_public_id_home_server"


def upgrade() -> None:
    op.drop_index(OLD_INDEX, table_name="offers")
    op.create_index(
        PER_HOME_INDEX,
        "offers",
        ["republished_from_offer_public_id", "home_server"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(PER_HOME_INDEX, table_name="offers")
    # This intentionally fails closed if both homes have already republished
    # the same source; a downgrade must not discard either independent offer.
    op.create_index(
        OLD_INDEX,
        "offers",
        ["republished_from_offer_public_id"],
        unique=True,
    )
