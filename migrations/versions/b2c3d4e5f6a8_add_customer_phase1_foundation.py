"""add customer phase1 foundation

Revision ID: b2c3d4e5f6a8
Revises: a7d9c4e2f1b3
Create Date: 2026-05-19 14:10:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM


revision: str = "b2c3d4e5f6a8"
down_revision: Union[str, None] = "a7d9c4e2f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    customer_relation_status = ENUM(
        "pending",
        "active",
        "expired",
        "revoked",
        "deleted",
        name="customerrelationstatus",
        create_type=False,
    )
    customer_tier = ENUM(
        "tier1",
        "tier2",
        name="customertier",
        create_type=False,
    )
    customer_relation_status.create(op.get_bind(), checkfirst=True)
    customer_tier.create(op.get_bind(), checkfirst=True)

    op.add_column("users", sa.Column("max_customers", sa.Integer(), server_default="5", nullable=False))

    op.create_table(
        "customer_relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("customer_user_id", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("invitation_token", sa.String(), nullable=False),
        sa.Column("management_name", sa.String(length=120), nullable=False),
        sa.Column("customer_tier", customer_tier, server_default="tier1", nullable=False),
        sa.Column("commission_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("min_trade_quantity", sa.Integer(), nullable=True),
        sa.Column("max_trade_quantity", sa.Integer(), nullable=True),
        sa.Column("max_daily_trades", sa.Integer(), nullable=True),
        sa.Column("max_daily_commodity_volume", sa.Integer(), nullable=True),
        sa.Column("trading_restricted_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", customer_relation_status, server_default="pending", nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invitation_token"),
    )
    op.create_index(op.f("ix_customer_relations_customer_user_id"), "customer_relations", ["customer_user_id"], unique=False)
    op.create_index(op.f("ix_customer_relations_created_by_user_id"), "customer_relations", ["created_by_user_id"], unique=False)
    op.create_index(op.f("ix_customer_relations_customer_tier"), "customer_relations", ["customer_tier"], unique=False)
    op.create_index(op.f("ix_customer_relations_expires_at"), "customer_relations", ["expires_at"], unique=False)
    op.create_index(op.f("ix_customer_relations_invitation_token"), "customer_relations", ["invitation_token"], unique=True)
    op.create_index(op.f("ix_customer_relations_owner_status"), "customer_relations", ["owner_user_id", "status"], unique=False)
    op.create_index(op.f("ix_customer_relations_status"), "customer_relations", ["status"], unique=False)
    op.create_index(
        "ix_customer_relations_customer_status",
        "customer_relations",
        ["customer_user_id", "status"],
        unique=False,
    )
    op.create_index(
        "ux_customer_relations_owner_management_active",
        "customer_relations",
        ["owner_user_id", "management_name"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ux_customer_relations_customer_active",
        "customer_relations",
        ["customer_user_id"],
        unique=True,
        postgresql_where=sa.text("customer_user_id IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_customer_relations_customer_active", table_name="customer_relations")
    op.drop_index("ux_customer_relations_owner_management_active", table_name="customer_relations")
    op.drop_index("ix_customer_relations_customer_status", table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_status"), table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_owner_status"), table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_invitation_token"), table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_expires_at"), table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_customer_tier"), table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_created_by_user_id"), table_name="customer_relations")
    op.drop_index(op.f("ix_customer_relations_customer_user_id"), table_name="customer_relations")
    op.drop_table("customer_relations")

    op.drop_column("users", "max_customers")

    ENUM(
        "tier1",
        "tier2",
        name="customertier",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)
    ENUM(
        "pending",
        "active",
        "expired",
        "revoked",
        "deleted",
        name="customerrelationstatus",
        create_type=False,
    ).drop(op.get_bind(), checkfirst=True)