"""add short-lived pre-auth Telegram queue sources

Revision ID: a274f5a6b8c9
Revises: a163f4a5b7c8
Create Date: 2026-07-19 09:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a274f5a6b8c9"
down_revision: Union[str, Sequence[str], None] = "a163f4a5b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_ACTIONS = (
    "callback_deadline", "otp_deadline", "offer_publish", "offer_success",
    "offer_validation_response", "offer_expiry_callback", "offer_repeat_response",
    "general_immediate", "trade_result", "trade_response", "trade_alternative",
    "trade_unavailable", "trade_noncritical", "partial_offer_edit",
    "traded_offer_edit", "expired_offer_edit", "cancelled_offer_edit",
    "other_active_offer_edit", "invalid_action_button_edit", "reconciliation_edit",
    "new_user_membership", "account_status", "channel_member_ban",
    "channel_member_unban", "targeted_admin_message", "admin_broadcast",
    "general_announcement", "market_transition", "market_status_correction",
    "noncritical_market", "timed_security", "delayed_restriction",
    "temporary_cleanup", "cosmetic_cleanup",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # PostgreSQL requires enum additions to commit before application writes
    # may use them.  The migration itself does not insert either value.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
            "'preauth_interaction'"
        )
        op.execute(
            "ALTER TYPE telegramdeliveryaction ADD VALUE IF NOT EXISTS "
            "'preauth_interaction_edit'"
        )
    op.drop_constraint(
        "ck_telegram_scheduled_operations_action",
        "telegram_scheduled_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_scheduled_operations_action",
        "telegram_scheduled_operations",
        "action_kind IN ('noncritical_market', 'preauth_interaction', "
        "'preauth_interaction_edit', 'temporary_cleanup', 'cosmetic_cleanup')",
    )
    op.drop_constraint(
        "ck_telegram_scheduled_operations_method",
        "telegram_scheduled_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_scheduled_operations_method",
        "telegram_scheduled_operations",
        "method IN ('sendMessage', 'editMessageText', 'deleteMessage', "
        "'editMessageReplyMarkup')",
    )


def downgrade() -> None:
    # Fail closed instead of deleting or silently reclassifying live source
    # evidence.  These sources expire within five minutes, so rollback runbook
    # drains them before schema downgrade.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM telegram_delivery_jobs
                    WHERE action_kind::text IN (
                        'preauth_interaction', 'preauth_interaction_edit'
                    )
                ) OR EXISTS (
                    SELECT 1 FROM telegram_scheduled_operations
                    WHERE action_kind IN (
                        'preauth_interaction', 'preauth_interaction_edit'
                    )
                ) THEN
                    RAISE EXCEPTION
                        'preauth Telegram sources must be drained before downgrade';
                END IF;
            END
            $$
            """
        )
    )
    op.drop_constraint(
        "ck_telegram_scheduled_operations_action",
        "telegram_scheduled_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_scheduled_operations_action",
        "telegram_scheduled_operations",
        "action_kind IN ('noncritical_market', 'temporary_cleanup', "
        "'cosmetic_cleanup')",
    )
    op.drop_constraint(
        "ck_telegram_scheduled_operations_method",
        "telegram_scheduled_operations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_scheduled_operations_method",
        "telegram_scheduled_operations",
        "method IN ('sendMessage', 'deleteMessage', "
        "'editMessageReplyMarkup')",
    )
    # PostgreSQL binds enum literals inside this check constraint to the old
    # enum OID.  Remove and recreate it around the type rebuild; leaving it in
    # place makes ``action_kind -> text`` fail even on an empty table.
    op.drop_constraint(
        "ck_telegram_delivery_jobs_editor_route",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.execute(
        "ALTER TABLE telegram_delivery_jobs "
        "ALTER COLUMN action_kind TYPE text USING action_kind::text"
    )
    op.execute("DROP TYPE telegramdeliveryaction")
    op.execute(
        f"CREATE TYPE telegramdeliveryaction AS ENUM ({_quoted(_OLD_ACTIONS)})"
    )
    op.execute(
        "ALTER TABLE telegram_delivery_jobs ALTER COLUMN action_kind "
        "TYPE telegramdeliveryaction USING action_kind::telegramdeliveryaction"
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_editor_route",
        "telegram_delivery_jobs",
        "bot_identity = 'primary' OR ("
        "destination_class = 'channel' AND "
        "method IN ('editMessageText', 'editMessageReplyMarkup') AND "
        "action_kind IN ('partial_offer_edit', 'traded_offer_edit', "
        "'expired_offer_edit', 'cancelled_offer_edit', "
        "'other_active_offer_edit', 'invalid_action_button_edit', "
        "'reconciliation_edit'))",
    )
