"""add offer and trade settlement type

Revision ID: b9e0f1a2c3d4
Revises: a8d9e0f1b2c3
Create Date: 2026-07-13 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b9e0f1a2c3d4"
down_revision: Union[str, Sequence[str], None] = "a8d9e0f1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


settlement_type = postgresql.ENUM(
    "CASH",
    "TOMORROW",
    name="settlementtype",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    settlement_type.create(bind, checkfirst=True)

    for table_name in ("offers", "trades"):
        op.add_column(
            table_name,
            sa.Column(
                "settlement_type",
                settlement_type,
                nullable=False,
                server_default=sa.text("'CASH'::settlementtype"),
            ),
        )


def downgrade() -> None:
    for table_name in ("trades", "offers"):
        op.drop_column(table_name, "settlement_type")

    settlement_type.drop(op.get_bind(), checkfirst=True)
