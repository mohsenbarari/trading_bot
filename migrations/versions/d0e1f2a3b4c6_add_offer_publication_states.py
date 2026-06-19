"""add offer publication states

Revision ID: d0e1f2a3b4c6
Revises: c8d9e0f1a2b3
Create Date: 2026-06-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "d0e1f2a3b4c6"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


offer_publication_surface = sa.Enum(
    "telegram_channel",
    "webapp_market",
    name="offerpublicationsurface",
)
offer_publication_status = sa.Enum(
    "pending",
    "sent",
    "visible",
    "failed",
    "disabled",
    "lagged",
    name="offerpublicationstatus",
)


def upgrade() -> None:
    op.create_table(
        "offer_publication_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("offer_id", sa.Integer(), nullable=True),
        sa.Column("offer_public_id", sa.String(length=40), nullable=False),
        sa.Column("offer_home_server", sa.String(length=16), nullable=False),
        sa.Column("surface", offer_publication_surface, nullable=False),
        sa.Column("publication_owner_server", sa.String(length=16), nullable=False),
        sa.Column("status", offer_publication_status, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("dedupe_key", sa.String(length=160), nullable=False),
        sa.Column("surface_resource_id", sa.String(length=160), nullable=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("offer_version_id", sa.Integer(), nullable=True),
        sa.Column("last_known_offer_status", sa.String(length=32), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lagged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=96), nullable=True),
        sa.Column("error_message", sa.String(length=240), nullable=True),
        sa.Column("state_metadata", sa.JSON(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="ux_offer_publication_states_dedupe_key"),
        sa.UniqueConstraint("offer_public_id", "surface", name="ux_offer_publication_states_offer_surface"),
    )
    op.create_index(op.f("ix_offer_publication_states_id"), "offer_publication_states", ["id"], unique=False)
    op.create_index(op.f("ix_offer_publication_states_offer_id"), "offer_publication_states", ["offer_id"], unique=False)
    op.create_index(op.f("ix_offer_publication_states_offer_public_id"), "offer_publication_states", ["offer_public_id"], unique=False)
    op.create_index(op.f("ix_offer_publication_states_offer_home_server"), "offer_publication_states", ["offer_home_server"], unique=False)
    op.create_index(op.f("ix_offer_publication_states_surface"), "offer_publication_states", ["surface"], unique=False)
    op.create_index(op.f("ix_offer_publication_states_publication_owner_server"), "offer_publication_states", ["publication_owner_server"], unique=False)
    op.create_index(op.f("ix_offer_publication_states_status"), "offer_publication_states", ["status"], unique=False)
    op.create_index("ix_offer_publication_states_offer_status", "offer_publication_states", ["offer_public_id", "status"], unique=False)
    op.create_index("ix_offer_publication_states_surface_status", "offer_publication_states", ["surface", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_offer_publication_states_surface_status", table_name="offer_publication_states")
    op.drop_index("ix_offer_publication_states_offer_status", table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_status"), table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_publication_owner_server"), table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_surface"), table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_offer_home_server"), table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_offer_public_id"), table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_offer_id"), table_name="offer_publication_states")
    op.drop_index(op.f("ix_offer_publication_states_id"), table_name="offer_publication_states")
    op.drop_table("offer_publication_states")
    offer_publication_status.drop(op.get_bind(), checkfirst=True)
    offer_publication_surface.drop(op.get_bind(), checkfirst=True)
