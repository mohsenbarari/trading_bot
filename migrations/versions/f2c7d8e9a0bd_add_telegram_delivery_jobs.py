"""add foreign-local Telegram delivery jobs

Revision ID: f2c7d8e9a0bd
Revises: f1b6e7f8a9dc
Create Date: 2026-07-16 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "f2c7d8e9a0bd"
down_revision: Union[str, Sequence[str], None] = "f1b6e7f8a9dc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


telegram_delivery_feeder_kind = postgresql.ENUM(
    "offer_control",
    "offer_edit",
    "trade",
    "admin_system",
    "market_status",
    "timed_bot",
    "direct",
    name="telegramdeliveryfeederkind",
    create_type=False,
)
telegram_delivery_action = postgresql.ENUM(
    "callback_deadline",
    "otp_deadline",
    "offer_publish",
    "offer_success",
    "offer_validation_response",
    "offer_expiry_callback",
    "offer_repeat_response",
    "general_immediate",
    "trade_result",
    "trade_response",
    "trade_alternative",
    "trade_unavailable",
    "trade_noncritical",
    "partial_offer_edit",
    "traded_offer_edit",
    "expired_offer_edit",
    "cancelled_offer_edit",
    "other_active_offer_edit",
    "invalid_action_button_edit",
    "reconciliation_edit",
    "new_user_membership",
    "account_status",
    "targeted_admin_message",
    "admin_broadcast",
    "general_announcement",
    "market_transition",
    "market_status_correction",
    "noncritical_market",
    "timed_security",
    "delayed_restriction",
    "temporary_cleanup",
    "cosmetic_cleanup",
    name="telegramdeliveryaction",
    create_type=False,
)
telegram_destination_class = postgresql.ENUM(
    "private",
    "channel",
    "admin",
    name="telegramdestinationclass",
    create_type=False,
)
telegram_delivery_state = postgresql.ENUM(
    "pending",
    "leased",
    "pending_retry",
    "pending_reconcile",
    "ambiguous",
    "ambiguous_unresolved",
    "sent",
    "sent_noop",
    "superseded",
    "expired_interaction",
    "permanent_undeliverable",
    "terminal_failed",
    "quarantined",
    "blocked_destination",
    "blocked_bot",
    "blocked_gateway",
    name="telegramdeliverystate",
    create_type=False,
)


def _create_enum_if_missing(name: str, values: tuple[str, ...]) -> None:
    rendered_values = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    op.execute(
        sa.text(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{name}') THEN
                    CREATE TYPE {name} AS ENUM ({rendered_values});
                END IF;
            END
            $$;
            """
        )
    )


def upgrade() -> None:
    _create_enum_if_missing(
        "telegramdeliveryfeederkind",
        ("offer_control", "offer_edit", "trade", "admin_system", "market_status", "timed_bot", "direct"),
    )
    _create_enum_if_missing(
        "telegramdeliveryaction",
        (
            "callback_deadline", "otp_deadline", "offer_publish", "offer_success",
            "offer_validation_response", "offer_expiry_callback", "offer_repeat_response",
            "general_immediate", "trade_result", "trade_response", "trade_alternative",
            "trade_unavailable", "trade_noncritical", "partial_offer_edit", "traded_offer_edit",
            "expired_offer_edit", "cancelled_offer_edit", "other_active_offer_edit",
            "invalid_action_button_edit", "reconciliation_edit", "new_user_membership",
            "account_status", "targeted_admin_message", "admin_broadcast", "general_announcement",
            "market_transition", "market_status_correction", "noncritical_market", "timed_security",
            "delayed_restriction", "temporary_cleanup", "cosmetic_cleanup",
        ),
    )
    _create_enum_if_missing("telegramdestinationclass", ("private", "channel", "admin"))
    _create_enum_if_missing(
        "telegramdeliverystate",
        (
            "pending", "leased", "pending_retry", "pending_reconcile", "ambiguous",
            "ambiguous_unresolved", "sent", "sent_noop", "superseded",
            "expired_interaction", "permanent_undeliverable", "terminal_failed",
            "quarantined", "blocked_destination", "blocked_bot", "blocked_gateway",
        ),
    )

    sequence = sa.Sequence("telegram_delivery_jobs_enqueued_seq_seq")
    sequence.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "telegram_delivery_jobs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "enqueued_seq",
            sa.BigInteger(),
            server_default=sa.text("nextval('telegram_delivery_jobs_enqueued_seq_seq')"),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=1024), nullable=False),
        sa.Column("feeder_kind", telegram_delivery_feeder_kind, nullable=False),
        sa.Column("feeder_rank", sa.SmallInteger(), nullable=False),
        sa.Column("source_natural_id", sa.String(length=256), nullable=False),
        sa.Column("source_version", sa.BigInteger(), nullable=False),
        sa.Column("action_kind", telegram_delivery_action, nullable=False),
        sa.Column("bot_identity", sa.String(length=128), nullable=False),
        sa.Column("destination_key", sa.String(length=256), nullable=False),
        sa.Column("destination_class", telegram_destination_class, nullable=False),
        sa.Column("method", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("priority_rank", sa.SmallInteger(), nullable=False),
        sa.Column("delivery_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("campaign_id", sa.String(length=192), nullable=True),
        sa.Column("run_id", sa.String(length=192), nullable=True),
        sa.Column("state", telegram_delivery_state, server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_ok", sa.Boolean(), nullable=True),
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        sa.Column("provider_error_code", sa.Integer(), nullable=True),
        sa.Column("provider_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_retry_after_seconds", sa.BigInteger(), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("outcome_reason", sa.String(length=160), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_redacted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("source_version >= 0", name="ck_telegram_delivery_jobs_source_version"),
        sa.CheckConstraint("feeder_rank >= 0", name="ck_telegram_delivery_jobs_feeder_rank"),
        sa.CheckConstraint("priority BETWEEN 0 AND 7", name="ck_telegram_delivery_jobs_priority"),
        sa.CheckConstraint("priority_rank >= 0", name="ck_telegram_delivery_jobs_priority_rank"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_telegram_delivery_jobs_attempt_count"),
        sa.CheckConstraint("lease_token >= 0", name="ck_telegram_delivery_jobs_lease_token"),
        sa.CheckConstraint(
            "bot_identity IN ('primary', 'channel_editor')",
            name="ck_telegram_delivery_jobs_bot_identity",
        ),
        sa.CheckConstraint(
            "bot_identity = 'primary' OR ("
            "destination_class = 'channel' AND "
            "method IN ('editMessageText', 'editMessageReplyMarkup') AND "
            "action_kind IN ('partial_offer_edit', 'traded_offer_edit', "
            "'expired_offer_edit', 'cancelled_offer_edit', 'other_active_offer_edit', "
            "'invalid_action_button_edit', 'reconciliation_edit'))",
            name="ck_telegram_delivery_jobs_editor_route",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="ux_telegram_delivery_jobs_dedupe_key"),
        sa.UniqueConstraint(
            "feeder_kind", "source_natural_id", "source_version", "action_kind", "destination_key",
            name="ux_telegram_delivery_jobs_logical_identity",
        ),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_claim",
        "telegram_delivery_jobs",
        [
            "bot_identity",
            "priority",
            "priority_rank",
            "delivery_deadline_at",
            "eligible_at",
            "next_retry_at",
            "enqueued_seq",
        ],
        unique=False,
        postgresql_where=sa.text("state IN ('pending', 'pending_retry')"),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_lease_recovery",
        "telegram_delivery_jobs",
        ["lease_until", "id"],
        unique=False,
        postgresql_where=sa.text("state = 'leased' AND lease_until IS NOT NULL"),
    )
    op.create_index(
        "ix_telegram_delivery_jobs_source",
        "telegram_delivery_jobs",
        ["feeder_kind", "source_natural_id", "source_version"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_delivery_jobs_campaign",
        "telegram_delivery_jobs",
        ["campaign_id", "state", "enqueued_seq"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_delivery_jobs_run",
        "telegram_delivery_jobs",
        ["run_id", "state"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_delivery_jobs_bot_destination_state",
        "telegram_delivery_jobs",
        ["bot_identity", "destination_key", "state", "next_retry_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_delivery_jobs_bot_destination_state", table_name="telegram_delivery_jobs")
    op.drop_index("ix_telegram_delivery_jobs_run", table_name="telegram_delivery_jobs")
    op.drop_index("ix_telegram_delivery_jobs_campaign", table_name="telegram_delivery_jobs")
    op.drop_index("ix_telegram_delivery_jobs_source", table_name="telegram_delivery_jobs")
    op.drop_index("ix_telegram_delivery_jobs_lease_recovery", table_name="telegram_delivery_jobs")
    op.drop_index("ix_telegram_delivery_jobs_claim", table_name="telegram_delivery_jobs")
    op.drop_table("telegram_delivery_jobs")
    sa.Sequence("telegram_delivery_jobs_enqueued_seq_seq").drop(op.get_bind(), checkfirst=True)
    telegram_delivery_state.drop(op.get_bind(), checkfirst=True)
    telegram_destination_class.drop(op.get_bind(), checkfirst=True)
    telegram_delivery_action.drop(op.get_bind(), checkfirst=True)
    telegram_delivery_feeder_kind.drop(op.get_bind(), checkfirst=True)
