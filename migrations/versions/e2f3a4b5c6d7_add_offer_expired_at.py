"""add offer expired_at

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-06-17 10:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_offers_expired_at"), "offers", ["expired_at"], unique=False)
    op.create_index("ix_offers_user_status_expired_at", "offers", ["user_id", "status", "expired_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_offers_user_status_expired_at", table_name="offers")
    op.drop_index(op.f("ix_offers_expired_at"), table_name="offers")
    op.drop_column("offers", "expired_at")
