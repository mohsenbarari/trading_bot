"""add durable multi-publisher Telegram dispatch outbox

Revision ID: f9a0b1c2d3e4
Revises: e8a4b5c6d7e9
Create Date: 2026-08-11 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f9a0b1c2d3e4"
down_revision: Union[str, Sequence[str], None] = "e8a4b5c6d7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PUBLISHER_IDENTITIES_SQL = (
    "'publisher_1', 'publisher_2', 'publisher_3', 'publisher_4', 'publisher_5'"
)
_PUBLISHER_OFFER_ACTIONS_SQL = (
    "'cancelled_offer_edit', 'expired_offer_edit', 'final_tail_channel_edit', "
    "'invalid_action_button_edit', 'offer_publish', 'other_active_offer_edit', "
    "'overtime_channel_edit', 'partial_offer_edit', 'reconciliation_edit', "
    "'traded_offer_edit'"
)
_DISPATCH_STATES_SQL = (
    "'pending', 'sent', 'acknowledged', 'retry_due', 'failed', 'superseded'"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_telegram_delivery_jobs_bot_identity",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_telegram_delivery_jobs_editor_route",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_bot_identity",
        "telegram_delivery_jobs",
        "bot_identity IN ('primary', 'channel_editor', "
        f"{_PUBLISHER_IDENTITIES_SQL})",
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_lane_route",
        "telegram_delivery_jobs",
        "bot_identity = 'primary' OR "
        "(bot_identity = 'channel_editor' AND "
        "destination_class = 'channel' AND "
        "method IN ('editMessageText', 'editMessageReplyMarkup') AND "
        "action_kind IN ('partial_offer_edit', 'traded_offer_edit', "
        "'expired_offer_edit', 'cancelled_offer_edit', 'other_active_offer_edit', "
        "'overtime_channel_edit', 'final_tail_channel_edit', "
        "'invalid_action_button_edit', 'reconciliation_edit')) OR "
        f"(bot_identity IN ({_PUBLISHER_IDENTITIES_SQL}) AND (("
        "destination_class = 'channel' AND "
        f"action_kind IN ({_PUBLISHER_OFFER_ACTIONS_SQL}) AND "
        "method IN ('sendMessage', 'editMessageText', "
        "'editMessageReplyMarkup', 'deleteMessage')) OR "
        "(destination_class = 'private' AND "
        "action_kind IN ('callback_deadline', 'offer_expiry_callback') AND "
        "method = 'answerCallbackQuery')))",
    )

    op.drop_constraint(
        "ck_offer_publication_states_publisher_bot_identity",
        "offer_publication_states",
        type_="check",
    )
    op.create_check_constraint(
        "ck_offer_publication_states_publisher_bot_identity",
        "offer_publication_states",
        "publisher_bot_identity IS NULL OR "
        "(surface = 'telegram_channel' AND publisher_bot_identity IN "
        f"('primary', {_PUBLISHER_IDENTITIES_SQL}))",
    )
    op.execute(
        """
        UPDATE offer_publication_states
        SET publisher_bot_identity = 'primary'
        WHERE surface = 'telegram_channel'
          AND publisher_bot_identity IS NULL
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_offer_publication_telegram_owner_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.surface <> 'telegram_channel' AND NEW.publisher_bot_identity IS NOT NULL THEN
                RAISE EXCEPTION 'non-Telegram publication cannot have a Telegram publisher';
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF OLD.surface = 'telegram_channel'
                   AND OLD.publisher_bot_identity IS NOT NULL
                   AND NEW.publisher_bot_identity IS DISTINCT FROM OLD.publisher_bot_identity THEN
                    RAISE EXCEPTION 'Telegram publication owner is immutable';
                END IF;

                IF OLD.surface = 'telegram_channel'
                   AND OLD.telegram_message_id IS NOT NULL
                   AND NEW.telegram_message_id IS DISTINCT FROM OLD.telegram_message_id THEN
                    RAISE EXCEPTION 'Telegram publication message identity is immutable';
                END IF;
            END IF;

            IF NEW.telegram_message_id IS NOT NULL
               AND NEW.publisher_bot_identity IS NULL THEN
                RAISE EXCEPTION 'Telegram publication message requires an owner';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_offer_publication_telegram_owner_immutable
        BEFORE INSERT OR UPDATE ON offer_publication_states
        FOR EACH ROW
        EXECUTE FUNCTION enforce_offer_publication_telegram_owner_immutable()
        """
    )

    op.create_table(
        "telegram_publisher_dispatch_commands",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("command_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("publisher_bot_identity", sa.String(length=32), nullable=False),
        sa.Column("dispatch_sequence", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("receipt_sequence", sa.BigInteger(), nullable=True),
        sa.Column("receipt_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(length=120), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            f"publisher_bot_identity IN ({_PUBLISHER_IDENTITIES_SQL})",
            name="ck_telegram_publisher_dispatch_commands_publisher",
        ),
        sa.CheckConstraint(
            f"state IN ({_DISPATCH_STATES_SQL})",
            name="ck_telegram_publisher_dispatch_commands_state",
        ),
        sa.CheckConstraint(
            "dispatch_sequence > 0 AND attempt_count >= 0 AND lease_token >= 0",
            name="ck_telegram_publisher_dispatch_commands_counters",
        ),
        sa.CheckConstraint(
            "(state = 'acknowledged') = (acknowledged_at IS NOT NULL)",
            name="ck_telegram_publisher_dispatch_commands_acknowledged_at",
        ),
        sa.CheckConstraint(
            "receipt_sequence IS NULL OR receipt_sequence > 0",
            name="ck_telegram_publisher_dispatch_commands_receipt_sequence",
        ),
        sa.CheckConstraint(
            "(receipt_sequence IS NULL) = (receipt_received_at IS NULL)",
            name="ck_telegram_publisher_dispatch_commands_receipt_timestamp",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["telegram_delivery_jobs.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "command_id",
            name="ux_telegram_publisher_dispatch_commands_command_id",
        ),
        sa.UniqueConstraint(
            "job_id",
            name="ux_telegram_publisher_dispatch_commands_job_id",
        ),
    )
    op.create_index(
        "ix_telegram_publisher_dispatch_commands_claim",
        "telegram_publisher_dispatch_commands",
        ["state", "next_retry_at", "id"],
        unique=False,
        postgresql_where=sa.text("state IN ('pending', 'retry_due')"),
    )
    op.create_index(
        "ix_telegram_publisher_dispatch_commands_lease_recovery",
        "telegram_publisher_dispatch_commands",
        ["lease_until", "id"],
        unique=False,
        postgresql_where=sa.text(
            "state IN ('pending', 'sent', 'retry_due') AND lease_until IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_telegram_publisher_dispatch_commands_lane_state",
        "telegram_publisher_dispatch_commands",
        ["publisher_bot_identity", "state", "next_retry_at", "id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION enforce_telegram_publisher_dispatch_command_owner()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            job_publisher text;
        BEGIN
            SELECT bot_identity INTO job_publisher
            FROM telegram_delivery_jobs
            WHERE id = NEW.job_id;

            IF NOT FOUND OR job_publisher <> NEW.publisher_bot_identity THEN
                RAISE EXCEPTION 'publisher dispatch command owner must match delivery job owner';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_telegram_publisher_dispatch_command_owner
        BEFORE INSERT OR UPDATE OF job_id, publisher_bot_identity
        ON telegram_publisher_dispatch_commands
        FOR EACH ROW
        EXECUTE FUNCTION enforce_telegram_publisher_dispatch_command_owner()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_telegram_delivery_job_dispatch_owner_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.bot_identity IS DISTINCT FROM OLD.bot_identity
               AND EXISTS (
                   SELECT 1
                   FROM telegram_publisher_dispatch_commands
                   WHERE job_id = OLD.id
               ) THEN
                RAISE EXCEPTION 'publisher delivery job owner is immutable after dispatch allocation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_telegram_delivery_job_dispatch_owner_immutable
        BEFORE UPDATE OF bot_identity ON telegram_delivery_jobs
        FOR EACH ROW
        EXECUTE FUNCTION enforce_telegram_delivery_job_dispatch_owner_immutable()
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM telegram_publisher_dispatch_commands)
               OR EXISTS (
                   SELECT 1 FROM telegram_delivery_jobs
                   WHERE bot_identity IN ({_PUBLISHER_IDENTITIES_SQL})
               )
               OR EXISTS (
                   SELECT 1 FROM offer_publication_states
                   WHERE publisher_bot_identity IN ({_PUBLISHER_IDENTITIES_SQL})
               ) THEN
                RAISE EXCEPTION
                    'multi-publisher Telegram evidence must be drained before downgrade';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "DROP TRIGGER trg_telegram_delivery_job_dispatch_owner_immutable "
        "ON telegram_delivery_jobs"
    )
    op.execute("DROP FUNCTION enforce_telegram_delivery_job_dispatch_owner_immutable()")
    op.execute(
        "DROP TRIGGER trg_telegram_publisher_dispatch_command_owner "
        "ON telegram_publisher_dispatch_commands"
    )
    op.execute("DROP FUNCTION enforce_telegram_publisher_dispatch_command_owner()")
    op.drop_index(
        "ix_telegram_publisher_dispatch_commands_lane_state",
        table_name="telegram_publisher_dispatch_commands",
    )
    op.drop_index(
        "ix_telegram_publisher_dispatch_commands_lease_recovery",
        table_name="telegram_publisher_dispatch_commands",
    )
    op.drop_index(
        "ix_telegram_publisher_dispatch_commands_claim",
        table_name="telegram_publisher_dispatch_commands",
    )
    op.drop_table("telegram_publisher_dispatch_commands")

    op.execute(
        "DROP TRIGGER trg_offer_publication_telegram_owner_immutable "
        "ON offer_publication_states"
    )
    op.execute("DROP FUNCTION enforce_offer_publication_telegram_owner_immutable()")
    op.drop_constraint(
        "ck_offer_publication_states_publisher_bot_identity",
        "offer_publication_states",
        type_="check",
    )
    op.create_check_constraint(
        "ck_offer_publication_states_publisher_bot_identity",
        "offer_publication_states",
        "publisher_bot_identity IS NULL OR "
        "(surface = 'telegram_channel' AND publisher_bot_identity = 'primary')",
    )

    op.drop_constraint(
        "ck_telegram_delivery_jobs_lane_route",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.drop_constraint(
        "ck_telegram_delivery_jobs_bot_identity",
        "telegram_delivery_jobs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_bot_identity",
        "telegram_delivery_jobs",
        "bot_identity IN ('primary', 'channel_editor')",
    )
    op.create_check_constraint(
        "ck_telegram_delivery_jobs_editor_route",
        "telegram_delivery_jobs",
        "bot_identity = 'primary' OR ("
        "destination_class = 'channel' AND "
        "method IN ('editMessageText', 'editMessageReplyMarkup') AND "
        "action_kind IN ('partial_offer_edit', 'traded_offer_edit', "
        "'expired_offer_edit', 'cancelled_offer_edit', 'other_active_offer_edit', "
        "'invalid_action_button_edit', 'reconciliation_edit'))",
    )
