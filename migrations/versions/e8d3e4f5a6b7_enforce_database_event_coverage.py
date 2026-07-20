"""Reject authoritative commits without a same-transaction DR event.

Revision ID: e8d3e4f5a6b7
Revises: e7c2d3e4f5a6
"""

import sqlalchemy as sa
from alembic import op


revision = "e8d3e4f5a6b7"
down_revision = "e7c2d3e4f5a6"
branch_labels = None
depends_on = None


EVENT_TABLES = (
    "accountant_relations", "admin_broadcast_messages", "admin_market_messages",
    "commodities", "commodity_aliases", "customer_relations", "invitations",
    "market_runtime_state", "market_schedule_overrides", "notifications",
    "offer_publication_states", "offer_requests", "offers", "trades",
    "trade_delivery_receipts", "telegram_link_tokens", "telegram_admin_broadcasts",
    "telegram_admin_broadcast_receipts", "telegram_notification_outbox",
    "trading_settings", "user_blocks", "user_notification_preferences", "users",
    "chat_files", "chat_members", "chats", "conversations",
    "invitation_identity_reservations", "invitation_sms_deliveries", "messages",
    "push_subscriptions",
    "session_login_requests", "single_session_recovery_admin_targets",
    "single_session_recovery_requests", "user_sessions",
)


def upgrade() -> None:
    op.add_column("dr_events", sa.Column("source_xid", sa.BigInteger()))
    op.create_index("ix_dr_events_source_xid", "dr_events", ["source_xid", "aggregate_type"])
    op.execute(
        """
        CREATE FUNCTION trading_bot_require_same_transaction_dr_event() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
            cfg dr_database_runtime%ROWTYPE;
            row_json jsonb;
            pk_columns text[];
            column_name text;
            identity_values jsonb := '[]'::jsonb;
            identity_value jsonb;
            aggregate_db_identity text;
        BEGIN
            SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
            IF NOT cfg.enforcement_enabled OR session_user <> cfg.application_role THEN
                RETURN NULL;
            END IF;
            IF current_setting('trading_bot.mutation_capability', true) <> 'writer' THEN
                RAISE EXCEPTION 'authoritative event coverage requires writer capability';
            END IF;
            IF TG_OP = 'DELETE' THEN
                row_json := to_jsonb(OLD);
            ELSE
                row_json := to_jsonb(NEW);
            END IF;
            SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
              INTO pk_columns
              FROM pg_index index_definition
              CROSS JOIN LATERAL unnest(index_definition.indkey)
                   WITH ORDINALITY AS key_column(attribute_number, ordinality)
              JOIN pg_attribute attribute
                ON attribute.attrelid = index_definition.indrelid
               AND attribute.attnum = key_column.attribute_number
             WHERE index_definition.indrelid = TG_RELID
               AND index_definition.indisprimary;
            IF pk_columns IS NULL OR array_length(pk_columns, 1) IS NULL THEN
                RAISE EXCEPTION 'authoritative table % lacks a primary key', TG_TABLE_NAME;
            END IF;
            FOREACH column_name IN ARRAY pk_columns LOOP
                identity_value := row_json -> column_name;
                IF identity_value IS NULL OR identity_value = 'null'::jsonb THEN
                    RAISE EXCEPTION 'authoritative table % has an incomplete primary key', TG_TABLE_NAME;
                END IF;
                identity_values := identity_values || jsonb_build_array(identity_value);
            END LOOP;
            IF jsonb_array_length(identity_values) = 1 THEN
                aggregate_db_identity := identity_values ->> 0;
            ELSE
                -- PostgreSQL jsonb renders the same scalar types used by the
                -- Python canonical identity helper.  Remove presentation-only
                -- spaces so composite keys match canonical_json_bytes().
                aggregate_db_identity := replace(identity_values::text, ', ', ',');
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM dr_events event
                 WHERE event.source_xid = txid_current()
                   AND event.aggregate_type = TG_TABLE_NAME
                   AND event.aggregate_db_id = aggregate_db_identity
                   AND event.operation = TG_OP
            ) THEN
                RAISE EXCEPTION 'authoritative mutation on %/% has no same-transaction DR event',
                    TG_TABLE_NAME, aggregate_db_identity;
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    for table in EVENT_TABLES:
        op.execute(
            "CREATE CONSTRAINT TRIGGER trg_three_site_event_coverage "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
            "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION trading_bot_require_same_transaction_dr_event()"
        )
    op.execute(
        "REVOKE ALL ON FUNCTION trading_bot_require_same_transaction_dr_event() FROM PUBLIC"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_dr_events_immutable ON dr_events")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_event_immutable()")
    op.execute(
        """
        CREATE FUNCTION trading_bot_dr_event_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.protocol_version = 2
               AND OLD.transaction_size = 0
               AND OLD.transaction_hash = repeat('0', 64)
               AND OLD.envelope_hash = repeat('0', 64)
               AND NEW.transaction_size > 0
               AND NEW.transaction_hash ~ '^[0-9a-f]{64}$'
               AND NEW.transaction_hash <> repeat('0', 64)
               AND NEW.envelope_hash ~ '^[0-9a-f]{64}$'
               AND NEW.envelope_hash <> repeat('0', 64)
               AND NEW.event_id IS NOT DISTINCT FROM OLD.event_id
               AND NEW.protocol_version IS NOT DISTINCT FROM OLD.protocol_version
               AND NEW.origin_authority IS NOT DISTINCT FROM OLD.origin_authority
               AND NEW.origin_physical_site IS NOT DISTINCT FROM OLD.origin_physical_site
               AND NEW.producer_epoch IS NOT DISTINCT FROM OLD.producer_epoch
               AND NEW.producer_sequence IS NOT DISTINCT FROM OLD.producer_sequence
               AND NEW.aggregate_type IS NOT DISTINCT FROM OLD.aggregate_type
               AND NEW.aggregate_id IS NOT DISTINCT FROM OLD.aggregate_id
               AND NEW.aggregate_db_id IS NOT DISTINCT FROM OLD.aggregate_db_id
               AND NEW.aggregate_version IS NOT DISTINCT FROM OLD.aggregate_version
               AND NEW.operation IS NOT DISTINCT FROM OLD.operation
               AND NEW.canonical_payload::jsonb IS NOT DISTINCT FROM OLD.canonical_payload::jsonb
               AND NEW.canonical_payload_hash IS NOT DISTINCT FROM OLD.canonical_payload_hash
               AND NEW.schema_version IS NOT DISTINCT FROM OLD.schema_version
               AND NEW.causation_id IS NOT DISTINCT FROM OLD.causation_id
               AND NEW.idempotency_key IS NOT DISTINCT FROM OLD.idempotency_key
               AND NEW.writer_epoch IS NOT DISTINCT FROM OLD.writer_epoch
               AND NEW.tombstone IS NOT DISTINCT FROM OLD.tombstone
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
               AND NEW.transaction_id IS NOT DISTINCT FROM OLD.transaction_id
               AND NEW.transaction_position IS NOT DISTINCT FROM OLD.transaction_position
               AND NEW.destination_streams IS NOT NULL
               AND NEW.destination_streams::jsonb <> '{}'::jsonb
               AND NEW.source_xid IS NOT DISTINCT FROM OLD.source_xid THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'dr_events are immutable';
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_dr_events_immutable BEFORE UPDATE OR DELETE ON dr_events "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_event_immutable()"
    )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_immutable() FROM PUBLIC")


def downgrade() -> None:
    for table in EVENT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_three_site_event_coverage ON {table}")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_require_same_transaction_dr_event()")
    # Remove the e8 function before dropping the column it references, then
    # restore the e6 transaction-finalization exception to immutability.
    op.execute("DROP TRIGGER IF EXISTS trg_dr_events_immutable ON dr_events")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_event_immutable()")
    op.execute(
        """
        CREATE FUNCTION trading_bot_dr_event_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF OLD.protocol_version = 2
               AND OLD.transaction_size = 0
               AND OLD.transaction_hash = repeat('0', 64)
               AND OLD.envelope_hash = repeat('0', 64)
               AND NEW.transaction_size > 0
               AND NEW.transaction_hash ~ '^[0-9a-f]{64}$'
               AND NEW.transaction_hash <> repeat('0', 64)
               AND NEW.envelope_hash ~ '^[0-9a-f]{64}$'
               AND NEW.envelope_hash <> repeat('0', 64)
               AND NEW.event_id IS NOT DISTINCT FROM OLD.event_id
               AND NEW.protocol_version IS NOT DISTINCT FROM OLD.protocol_version
               AND NEW.origin_authority IS NOT DISTINCT FROM OLD.origin_authority
               AND NEW.origin_physical_site IS NOT DISTINCT FROM OLD.origin_physical_site
               AND NEW.producer_epoch IS NOT DISTINCT FROM OLD.producer_epoch
               AND NEW.producer_sequence IS NOT DISTINCT FROM OLD.producer_sequence
               AND NEW.aggregate_type IS NOT DISTINCT FROM OLD.aggregate_type
               AND NEW.aggregate_id IS NOT DISTINCT FROM OLD.aggregate_id
               AND NEW.aggregate_db_id IS NOT DISTINCT FROM OLD.aggregate_db_id
               AND NEW.aggregate_version IS NOT DISTINCT FROM OLD.aggregate_version
               AND NEW.operation IS NOT DISTINCT FROM OLD.operation
               AND NEW.canonical_payload::jsonb IS NOT DISTINCT FROM OLD.canonical_payload::jsonb
               AND NEW.canonical_payload_hash IS NOT DISTINCT FROM OLD.canonical_payload_hash
               AND NEW.schema_version IS NOT DISTINCT FROM OLD.schema_version
               AND NEW.causation_id IS NOT DISTINCT FROM OLD.causation_id
               AND NEW.idempotency_key IS NOT DISTINCT FROM OLD.idempotency_key
               AND NEW.writer_epoch IS NOT DISTINCT FROM OLD.writer_epoch
               AND NEW.tombstone IS NOT DISTINCT FROM OLD.tombstone
               AND NEW.created_at IS NOT DISTINCT FROM OLD.created_at
               AND NEW.transaction_id IS NOT DISTINCT FROM OLD.transaction_id
               AND NEW.transaction_position IS NOT DISTINCT FROM OLD.transaction_position
               AND NEW.destination_streams IS NOT NULL
               AND NEW.destination_streams::jsonb <> '{}'::jsonb THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'dr_events are immutable';
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_dr_events_immutable BEFORE UPDATE OR DELETE ON dr_events "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_event_immutable()"
    )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_immutable() FROM PUBLIC")
    op.drop_index("ix_dr_events_source_xid", table_name="dr_events")
    op.drop_column("dr_events", "source_xid")
