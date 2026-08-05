"""add offer overtime data model

Additive only. It creates storage for the overtime feature but emits no new
state: every column defaults to the feature-disabled value and no application
code writes the new statuses yet. That lets both servers take the schema before
either can produce a value the other cannot parse.

Revision ID: b5d1c7e93f04
Revises: a274f5a6b8c9
Create Date: 2026-08-05 11:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5d1c7e93f04"
down_revision: Union[str, Sequence[str], None] = "a274f5a6b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_REQUEST_STATUSES = (
    "overtime_queued",
    "overtime_delivering",
    "overtime_presented",
    "overtime_rejected_by_owner",
    "overtime_decision_expired",
    "overtime_cancelled_by_requester",
    "overtime_invalidated",
    "overtime_delivery_expired",
    "overtime_rejected_requester_limit",
)

_NONTERMINAL = ("overtime_queued", "overtime_delivering", "overtime_presented")
_OWNER_OCCUPYING = ("overtime_delivering", "overtime_presented")


def _quoted(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # PostgreSQL requires enum additions to be committed before anything may
    # reference the new labels, including the index predicates created below.
    with op.get_context().autocommit_block():
        for status in _NEW_REQUEST_STATUSES:
            op.execute(
                f"ALTER TYPE offerrequeststatus ADD VALUE IF NOT EXISTS '{status}'"
            )

    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'offerrequestworkflow') THEN "
        "CREATE TYPE offerrequestworkflow AS ENUM ('direct', 'overtime'); "
        "END IF; END $$"
    )

    op.add_column(
        "users",
        sa.Column(
            "offer_overtime_minutes",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_check_constraint(
        "ck_users_offer_overtime_minutes_range",
        "users",
        "offer_overtime_minutes BETWEEN 0 AND 10",
    )

    op.add_column(
        "offers",
        sa.Column(
            "overtime_minutes_snapshot",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "offers",
        sa.Column(
            "overtime_trade_committed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_check_constraint(
        "ck_offers_overtime_minutes_snapshot_range",
        "offers",
        "overtime_minutes_snapshot BETWEEN 0 AND 10",
    )

    op.add_column("offer_requests", sa.Column("request_public_id", sa.String(length=40), nullable=True))
    op.add_column(
        "offer_requests",
        sa.Column(
            "workflow_kind",
            sa.Enum("direct", "overtime", name="offerrequestworkflow", create_type=False),
            nullable=False,
            server_default="direct",
        ),
    )
    op.add_column("offer_requests", sa.Column("offer_owner_user_id", sa.Integer(), nullable=True))
    op.add_column("offer_requests", sa.Column("queue_sequence", sa.BigInteger(), nullable=True))
    op.add_column("offer_requests", sa.Column("presented_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("offer_requests", sa.Column("decision_deadline_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("offer_requests", sa.Column("decided_by_user_id", sa.Integer(), nullable=True))
    op.add_column("offer_requests", sa.Column("terminal_reason", sa.String(length=64), nullable=True))
    op.add_column("offer_requests", sa.Column("telegram_delivery_job_id", sa.Integer(), nullable=True))
    op.add_column("offer_requests", sa.Column("telegram_message_id", sa.BigInteger(), nullable=True))

    op.create_foreign_key(
        "fk_offer_requests_offer_owner_user_id_users",
        "offer_requests",
        "users",
        ["offer_owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_offer_requests_decided_by_user_id_users",
        "offer_requests",
        "users",
        ["decided_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_offer_requests_telegram_delivery_job_id",
        "offer_requests",
        "telegram_delivery_jobs",
        ["telegram_delivery_job_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index(
        "ix_offer_requests_request_public_id",
        "offer_requests",
        ["request_public_id"],
        unique=True,
    )
    # Predicates compare the enum as text so they never bind to a specific enum
    # OID, which keeps a later type rebuild from invalidating them.
    op.create_index(
        "ux_offer_requests_overtime_active_per_offer",
        "offer_requests",
        ["request_home_server", "offer_public_id"],
        unique=True,
        postgresql_where=sa.text(f"result_status::text IN ({_quoted(_NONTERMINAL)})"),
    )
    op.create_index(
        "ux_offer_requests_overtime_owner_occupied",
        "offer_requests",
        ["request_home_server", "offer_owner_user_id"],
        unique=True,
        postgresql_where=sa.text(f"result_status::text IN ({_quoted(_OWNER_OCCUPYING)})"),
    )
    op.create_index(
        "ix_offer_requests_overtime_queue_order",
        "offer_requests",
        ["request_home_server", "offer_owner_user_id", "queue_sequence"],
        postgresql_where=sa.text("result_status::text = 'overtime_queued'"),
    )
    op.create_index(
        "ix_offer_requests_overtime_open_by_requester",
        "offer_requests",
        ["requester_user_id"],
        postgresql_where=sa.text(f"result_status::text IN ({_quoted(_NONTERMINAL)})"),
    )


def downgrade() -> None:
    # Fail closed rather than delete decision evidence. A live overtime request
    # would lose its audit trail and could still be acted on by the older code.
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM offer_requests
                    WHERE result_status::text IN ({_quoted(_NEW_REQUEST_STATUSES)})
                       OR workflow_kind::text = 'overtime'
                ) THEN
                    RAISE EXCEPTION
                        'overtime requests must be drained before schema downgrade';
                END IF;
            END
            $$
            """
        )
    )

    op.drop_index("ix_offer_requests_overtime_open_by_requester", table_name="offer_requests")
    op.drop_index("ix_offer_requests_overtime_queue_order", table_name="offer_requests")
    op.drop_index("ux_offer_requests_overtime_owner_occupied", table_name="offer_requests")
    op.drop_index("ux_offer_requests_overtime_active_per_offer", table_name="offer_requests")
    op.drop_index("ix_offer_requests_request_public_id", table_name="offer_requests")

    op.drop_constraint("fk_offer_requests_telegram_delivery_job_id", "offer_requests", type_="foreignkey")
    op.drop_constraint("fk_offer_requests_decided_by_user_id_users", "offer_requests", type_="foreignkey")
    op.drop_constraint("fk_offer_requests_offer_owner_user_id_users", "offer_requests", type_="foreignkey")

    for column in (
        "telegram_message_id",
        "telegram_delivery_job_id",
        "terminal_reason",
        "decided_by_user_id",
        "decision_deadline_at",
        "presented_at",
        "queue_sequence",
        "offer_owner_user_id",
        "workflow_kind",
        "request_public_id",
    ):
        op.drop_column("offer_requests", column)

    op.execute("DROP TYPE IF EXISTS offerrequestworkflow")

    op.drop_constraint("ck_offers_overtime_minutes_snapshot_range", "offers", type_="check")
    op.drop_column("offers", "overtime_trade_committed")
    op.drop_column("offers", "overtime_minutes_snapshot")

    op.drop_constraint("ck_users_offer_overtime_minutes_range", "users", type_="check")
    op.drop_column("users", "offer_overtime_minutes")

    # Enum labels on offerrequeststatus are intentionally retained. PostgreSQL
    # cannot remove a used label in place, and keeping them lets application
    # code roll back and forward without another type rebuild.
