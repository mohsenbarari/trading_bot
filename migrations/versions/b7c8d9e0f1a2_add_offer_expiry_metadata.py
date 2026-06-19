"""add offer expiry metadata

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-06-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("expired_by_user_id", sa.Integer(), nullable=True))
    op.add_column("offers", sa.Column("expired_by_actor_user_id", sa.Integer(), nullable=True))
    op.add_column("offers", sa.Column("expire_source_surface", sa.String(length=32), nullable=True))
    op.add_column("offers", sa.Column("expire_source_server", sa.String(length=16), nullable=True))
    op.create_index(op.f("ix_offers_expired_by_user_id"), "offers", ["expired_by_user_id"], unique=False)
    op.create_index(op.f("ix_offers_expired_by_actor_user_id"), "offers", ["expired_by_actor_user_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_offers_expired_by_user_id_users"),
        "offers",
        "users",
        ["expired_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_offers_expired_by_actor_user_id_users"),
        "offers",
        "users",
        ["expired_by_actor_user_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_offers_expired_by_actor_user_id_users"), "offers", type_="foreignkey")
    op.drop_constraint(op.f("fk_offers_expired_by_user_id_users"), "offers", type_="foreignkey")
    op.drop_index(op.f("ix_offers_expired_by_actor_user_id"), table_name="offers")
    op.drop_index(op.f("ix_offers_expired_by_user_id"), table_name="offers")
    op.drop_column("offers", "expire_source_server")
    op.drop_column("offers", "expire_source_surface")
    op.drop_column("offers", "expired_by_actor_user_id")
    op.drop_column("offers", "expired_by_user_id")
