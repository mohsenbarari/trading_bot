"""guard bounded sync publication publisher promotion

Revision ID: fe4f5a6b7c8d
Revises: fd3e4f5a6b7c
Create Date: 2026-08-21 23:10:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "fe4f5a6b7c8d"
down_revision: Union[str, Sequence[str], None] = "fd3e4f5a6b7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PUBLISHER_IDENTITIES_SQL = "'publisher_1', 'publisher_2', 'publisher_3', 'publisher_4', 'publisher_5'"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION enforce_offer_publication_telegram_owner_immutable()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.surface <> 'telegram_channel' AND NEW.publisher_bot_identity IS NOT NULL THEN
                RAISE EXCEPTION 'non-Telegram publication cannot have a Telegram publisher';
            END IF;

            IF TG_OP = 'INSERT'
               AND NEW.publisher_bot_identity IN ({_PUBLISHER_IDENTITIES_SQL}) THEN
                IF NEW.surface = 'telegram_channel'
                   AND NEW.publication_owner_server = 'foreign'
                   AND NEW.version_id > 1
                   AND NEW.offer_public_id IS NOT NULL
                   AND NEW.telegram_message_id IS NULL
                   AND NEW.dedupe_key = (
                       'offer-publication:telegram_channel:' || NEW.offer_public_id
                   )
                   AND COALESCE(
                       current_setting(
                           'trading_bot.sync_publication_publisher_promotion',
                           true
                       ),
                       ''
                   ) = NEW.dedupe_key THEN
                    NULL;
                ELSE
                    RAISE EXCEPTION 'Telegram publication owner is immutable';
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF OLD.surface = 'telegram_channel'
                   AND OLD.publisher_bot_identity IS NOT NULL
                   AND NEW.publisher_bot_identity IS DISTINCT FROM OLD.publisher_bot_identity THEN
                    IF OLD.publisher_bot_identity = 'primary'
                       AND OLD.publication_owner_server = 'foreign'
                       AND OLD.status = 'pending'
                       AND OLD.version_id = 1
                       AND OLD.telegram_message_id IS NULL
                       AND NEW.surface = 'telegram_channel'
                       AND NEW.publication_owner_server = 'foreign'
                       AND NEW.dedupe_key IS NOT DISTINCT FROM OLD.dedupe_key
                       AND NEW.offer_public_id IS NOT DISTINCT FROM OLD.offer_public_id
                       AND NEW.offer_public_id IS NOT NULL
                       AND NEW.telegram_message_id IS NULL
                       AND NEW.dedupe_key = (
                           'offer-publication:telegram_channel:' || NEW.offer_public_id
                       )
                       AND NEW.publisher_bot_identity IN ({_PUBLISHER_IDENTITIES_SQL})
                       AND NEW.version_id > OLD.version_id
                       AND COALESCE(
                           current_setting(
                               'trading_bot.sync_publication_publisher_promotion',
                               true
                           ),
                           ''
                       ) = NEW.dedupe_key THEN
                        PERFORM set_config(
                            'trading_bot.sync_publication_publisher_promotion',
                            '',
                            true
                        );
                    ELSE
                        RAISE EXCEPTION 'Telegram publication owner is immutable';
                    END IF;
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


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_offer_publication_telegram_owner_immutable()
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
