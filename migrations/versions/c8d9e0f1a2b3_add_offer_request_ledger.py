"""add offer request ledger

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c8d9e0f1a2b3"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


offer_request_status = postgresql.ENUM(
    "received",
    "authorized",
    "rejected_business_rule",
    "rejected_offer_expired",
    "rejected_lot_unavailable",
    "rejected_conflict",
    "completed_trade",
    "duplicate_replay",
    "failed_internal",
    name="offerrequeststatus",
    create_type=False,
)
offer_request_source_surface = postgresql.ENUM(
    "webapp",
    "telegram_bot",
    "internal_forward",
    name="offerrequestsourcesurface",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    offer_request_status.create(bind, checkfirst=True)
    offer_request_source_surface.create(bind, checkfirst=True)
    op.create_table(
        "offer_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("request_home_server", sa.String(length=16), nullable=False),
        sa.Column("local_offer_id", sa.Integer(), nullable=True),
        sa.Column("offer_public_id", sa.String(length=40), nullable=False),
        sa.Column("requester_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("request_source_surface", offer_request_source_surface, nullable=False),
        sa.Column("request_source_server", sa.String(length=16), nullable=False),
        sa.Column("requested_quantity", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_status", offer_request_status, nullable=False, server_default=sa.text("'received'")),
        sa.Column("public_failure_code", sa.String(length=64), nullable=True),
        sa.Column("public_failure_message", sa.String(length=240), nullable=True),
        sa.Column("internal_failure_code", sa.String(length=96), nullable=True),
        sa.Column("internal_failure_context", sa.JSON(), nullable=True),
        sa.Column("resulting_trade_id", sa.Integer(), nullable=True),
        sa.Column("customer_relation_id", sa.Integer(), nullable=True),
        sa.Column("customer_owner_user_id", sa.Integer(), nullable=True),
        sa.Column("customer_tier_snapshot", sa.String(length=32), nullable=True),
        sa.Column("customer_management_name_snapshot", sa.String(length=120), nullable=True),
        sa.Column("customer_commission_rate_snapshot", sa.Numeric(5, 2), nullable=True),
        sa.Column("customer_commission_context", sa.JSON(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("requested_quantity > 0", name="ck_offer_requests_requested_quantity_positive"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_owner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_relation_id"], ["customer_relations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["local_offer_id"], ["offers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requester_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resulting_trade_id"], ["trades.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_offer_requests_id"), "offer_requests", ["id"], unique=False)
    op.create_index(op.f("ix_offer_requests_request_home_server"), "offer_requests", ["request_home_server"], unique=False)
    op.create_index("ix_offer_requests_offer_public_id", "offer_requests", ["offer_public_id"], unique=False)
    op.create_index("ix_offer_requests_local_offer_id", "offer_requests", ["local_offer_id"], unique=False)
    op.create_index("ix_offer_requests_requester_user_id", "offer_requests", ["requester_user_id"], unique=False)
    op.create_index("ix_offer_requests_actor_user_id", "offer_requests", ["actor_user_id"], unique=False)
    op.create_index("ix_offer_requests_received_at", "offer_requests", ["received_at"], unique=False)
    op.create_index("ix_offer_requests_result_status", "offer_requests", ["result_status"], unique=False)
    op.create_index("ix_offer_requests_resulting_trade_id", "offer_requests", ["resulting_trade_id"], unique=False)
    op.create_index("ix_offer_requests_customer_relation_id", "offer_requests", ["customer_relation_id"], unique=False)
    op.create_index("ix_offer_requests_customer_owner_user_id", "offer_requests", ["customer_owner_user_id"], unique=False)
    op.create_index(
        "ux_offer_requests_home_idempotency_key",
        "offer_requests",
        ["request_home_server", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_offer_requests_home_idempotency_key", table_name="offer_requests")
    op.drop_index("ix_offer_requests_customer_owner_user_id", table_name="offer_requests")
    op.drop_index("ix_offer_requests_customer_relation_id", table_name="offer_requests")
    op.drop_index("ix_offer_requests_resulting_trade_id", table_name="offer_requests")
    op.drop_index("ix_offer_requests_result_status", table_name="offer_requests")
    op.drop_index("ix_offer_requests_received_at", table_name="offer_requests")
    op.drop_index("ix_offer_requests_actor_user_id", table_name="offer_requests")
    op.drop_index("ix_offer_requests_requester_user_id", table_name="offer_requests")
    op.drop_index("ix_offer_requests_local_offer_id", table_name="offer_requests")
    op.drop_index("ix_offer_requests_offer_public_id", table_name="offer_requests")
    op.drop_index(op.f("ix_offer_requests_request_home_server"), table_name="offer_requests")
    op.drop_index(op.f("ix_offer_requests_id"), table_name="offer_requests")
    op.drop_table("offer_requests")
    offer_request_source_surface.drop(op.get_bind(), checkfirst=True)
    offer_request_status.drop(op.get_bind(), checkfirst=True)
