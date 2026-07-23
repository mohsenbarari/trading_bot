"""add canonical publisher identity to offer publication state

Revision ID: f3d8e9a0b1ce
Revises: f2c7d8e9a0bd
Create Date: 2026-07-17 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3d8e9a0b1ce"
down_revision: Union[str, Sequence[str], None] = "f2c7d8e9a0bd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "offer_publication_states",
        sa.Column("publisher_bot_identity", sa.String(length=32), nullable=True),
    )
    # Install the constraint while every existing row still has NULL in the
    # new nullable column.  PostgreSQL refuses a later ALTER TABLE when the
    # backfill has queued trigger events in this same Alembic transaction.
    # Creating it first also makes PostgreSQL enforce the invariant while the
    # backfill runs.
    op.create_check_constraint(
        "ck_offer_publication_states_publisher_bot_identity",
        "offer_publication_states",
        "publisher_bot_identity IS NULL OR "
        "(surface = 'telegram_channel' AND publisher_bot_identity = 'primary')",
    )
    op.execute(
        sa.text(
            """
            UPDATE offer_publication_states
            SET publisher_bot_identity = 'primary'
            WHERE surface = 'telegram_channel'
              AND publisher_bot_identity IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_offer_publication_states_publisher_bot_identity",
        "offer_publication_states",
        type_="check",
    )
    op.drop_column("offer_publication_states", "publisher_bot_identity")
