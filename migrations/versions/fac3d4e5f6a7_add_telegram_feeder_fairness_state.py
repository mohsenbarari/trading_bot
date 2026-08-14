"""add durable Telegram feeder fairness state

Revision ID: fac3d4e5f6a7
Revises: fab2c3d4e5f6
Create Date: 2026-07-18 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fac3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "fab2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_delivery_feeder_states",
        sa.Column("feeder_kind", sa.String(length=32), nullable=False),
        sa.Column(
            "fresh_success_counts",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "feeder_kind",
            name="pk_telegram_delivery_feeder_states",
        ),
        sa.CheckConstraint(
            "feeder_kind IN ('offer_edit')",
            name="ck_telegram_delivery_feeder_states_kind",
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO telegram_delivery_feeder_states "
            "(feeder_kind, fresh_success_counts) VALUES "
            "('offer_edit', '{}'::json)"
        )
    )


def downgrade() -> None:
    op.drop_table("telegram_delivery_feeder_states")
