"""add durable Telegram interaction anchor generations

Revision ID: fb07b8c9d0e1
Revises: faf6a7b8c9d0
Create Date: 2026-07-18 23:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "fb07b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "faf6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_interaction_anchor_states",
        sa.Column("chat_id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=True),
        sa.Column("desired_generation", sa.BigInteger(), nullable=False),
        sa.Column("desired_outbox_id", sa.Integer(), nullable=True),
        sa.Column("desired_logical_message_key", sa.String(length=192), nullable=False),
        sa.Column("active_generation", sa.BigInteger(), nullable=True),
        sa.Column("active_outbox_id", sa.Integer(), nullable=True),
        sa.Column("active_message_id", sa.BigInteger(), nullable=True),
        sa.Column("active_logical_message_key", sa.String(length=192), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "chat_id <> 0",
            name="ck_telegram_interaction_anchor_states_chat_id",
        ),
        sa.CheckConstraint(
            "desired_generation > 0",
            name="ck_telegram_interaction_anchor_states_desired_generation",
        ),
        sa.CheckConstraint(
            "active_generation IS NULL OR active_generation > 0",
            name="ck_telegram_interaction_anchor_states_active_generation",
        ),
        sa.CheckConstraint(
            "active_message_id IS NULL OR active_message_id > 0",
            name="ck_telegram_interaction_anchor_states_active_message_id",
        ),
        sa.CheckConstraint(
            "active_generation IS NULL OR active_generation <= desired_generation",
            name="ck_telegram_interaction_anchor_states_generation_order",
        ),
        sa.CheckConstraint(
            "((active_generation IS NULL AND active_message_id IS NULL AND "
            "active_logical_message_key IS NULL) OR "
            "(active_generation IS NOT NULL AND active_message_id IS NOT NULL "
            "AND active_logical_message_key IS NOT NULL))",
            name="ck_telegram_interaction_anchor_states_active_tuple",
        ),
        sa.CheckConstraint(
            "active_generation IS NOT NULL OR active_outbox_id IS NULL",
            name="ck_telegram_interaction_anchor_active_outbox",
        ),
        sa.ForeignKeyConstraint(
            ["active_outbox_id"],
            ["telegram_notification_outbox.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["desired_outbox_id"],
            ["telegram_notification_outbox.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("chat_id"),
    )
    op.create_index(
        "ix_telegram_interaction_anchor_states_recipient",
        "telegram_interaction_anchor_states",
        ["recipient_user_id"],
        unique=False,
    )
    op.create_index(
        "ux_telegram_interaction_anchor_states_desired_outbox",
        "telegram_interaction_anchor_states",
        ["desired_outbox_id"],
        unique=True,
        postgresql_where=sa.text("desired_outbox_id IS NOT NULL"),
    )
    op.create_index(
        "ux_telegram_interaction_anchor_states_active_outbox",
        "telegram_interaction_anchor_states",
        ["active_outbox_id"],
        unique=True,
        postgresql_where=sa.text("active_outbox_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ux_telegram_interaction_anchor_states_active_outbox",
        table_name="telegram_interaction_anchor_states",
    )
    op.drop_index(
        "ux_telegram_interaction_anchor_states_desired_outbox",
        table_name="telegram_interaction_anchor_states",
    )
    op.drop_index(
        "ix_telegram_interaction_anchor_states_recipient",
        table_name="telegram_interaction_anchor_states",
    )
    op.drop_table("telegram_interaction_anchor_states")
