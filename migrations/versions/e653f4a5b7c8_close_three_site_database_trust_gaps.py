"""Close three-site database trust, entitlement, and trigger-mode gaps.

Revision ID: e653f4a5b7c8
Revises: d542e3f4a6b7
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "e653f4a5b7c8"
down_revision = "d542e3f4a6b7"
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
    "push_subscriptions", "session_login_requests",
    "single_session_recovery_admin_targets", "single_session_recovery_requests",
    "user_sessions",
)

WEBAPP_ONLY_TABLES = (
    "chat_files", "chat_members", "chats", "conversations",
    "invitation_identity_reservations", "invitation_sms_deliveries", "messages",
    "push_subscriptions", "session_login_requests",
    "single_session_recovery_admin_targets", "single_session_recovery_requests",
    "user_sessions",
)


def _quoted_list(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


REQUIRED_DESTINATIONS_SQL = f"""
CREATE OR REPLACE FUNCTION trading_bot_required_dr_destinations(
    origin_site text,
    aggregate_name text
) RETURNS text[]
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER
SET search_path = public, pg_temp AS $$
BEGIN
    IF aggregate_name = ANY(ARRAY[{_quoted_list(WEBAPP_ONLY_TABLES)}]::text[]) THEN
        IF origin_site = 'webapp_fi' THEN RETURN ARRAY['webapp_ir']::text[]; END IF;
        IF origin_site = 'webapp_ir' THEN RETURN ARRAY['webapp_fi']::text[]; END IF;
        RETURN ARRAY[]::text[];
    END IF;
    IF origin_site = 'bot_fi' THEN
        RETURN ARRAY['webapp_fi','webapp_ir']::text[];
    ELSIF origin_site = 'webapp_fi' THEN
        RETURN ARRAY['bot_fi','webapp_ir']::text[];
    ELSIF origin_site = 'webapp_ir' THEN
        RETURN ARRAY['bot_fi','webapp_fi']::text[];
    END IF;
    RETURN ARRAY[]::text[];
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_dr_event_entitlement_valid(checked_event_id text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    event_row dr_events%ROWTYPE;
    supplied_destinations text[];
    required_destinations text[];
BEGIN
    SELECT * INTO event_row FROM dr_events WHERE event_id = checked_event_id;
    IF NOT FOUND
       OR event_row.protocol_version IS DISTINCT FROM 2
       OR event_row.origin_physical_site NOT IN ('bot_fi','webapp_fi','webapp_ir')
       OR event_row.origin_authority IS DISTINCT FROM (
          CASE WHEN event_row.origin_physical_site = 'bot_fi' THEN 'foreign' ELSE 'webapp' END
       )
       OR event_row.destination_streams IS NULL
       OR jsonb_typeof(event_row.destination_streams::jsonb) <> 'object' THEN
        RETURN false;
    END IF;
    SELECT COALESCE(array_agg(destination.key ORDER BY destination.key), ARRAY[]::text[])
      INTO supplied_destinations
      FROM jsonb_object_keys(event_row.destination_streams::jsonb) AS destination(key);
    SELECT COALESCE(array_agg(destination.site ORDER BY destination.site), ARRAY[]::text[])
      INTO required_destinations
      FROM unnest(trading_bot_required_dr_destinations(
          event_row.origin_physical_site, event_row.aggregate_type
      )) AS destination(site);
    RETURN supplied_destinations = required_destinations;
EXCEPTION
    WHEN data_exception OR invalid_text_representation THEN
        RETURN false;
END;
$$;
"""


PAYLOAD_MATCH_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_projection_payload_matches(
    checked_table text,
    source_row jsonb,
    event_payload jsonb
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    expected_keys text[];
    actual_keys text[];
    field_name text;
    source_value jsonb;
    supplied_value jsonb;
    enum_column boolean;
    column_type text;
BEGIN
    IF jsonb_typeof(source_row) <> 'object' OR jsonb_typeof(event_payload) <> 'object' THEN
        RETURN false;
    END IF;
    SELECT array_agg(column_name ORDER BY column_name)
      INTO expected_keys
      FROM dr_projection_field_allowlist
     WHERE table_name = checked_table;
    SELECT array_agg(key ORDER BY key)
      INTO actual_keys
      FROM jsonb_object_keys(event_payload) AS fields(key);
    IF expected_keys IS NULL OR actual_keys IS DISTINCT FROM expected_keys THEN
        RETURN false;
    END IF;
    FOREACH field_name IN ARRAY expected_keys LOOP
        source_value := source_row -> field_name;
        supplied_value := event_payload -> field_name;
        IF source_value IS NOT DISTINCT FROM supplied_value THEN CONTINUE; END IF;
        IF jsonb_typeof(source_value) = 'number'
           AND jsonb_typeof(supplied_value) = 'string'
           AND source_value #>> '{}' = supplied_value #>> '{}' THEN
            CONTINUE;
        END IF;
        SELECT data_type.typtype='e', data_type.typname
          INTO enum_column, column_type
          FROM pg_class relation
          JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace
          JOIN pg_attribute attribute ON attribute.attrelid=relation.oid
          JOIN pg_type data_type ON data_type.oid=attribute.atttypid
         WHERE namespace.nspname='public'
           AND relation.relname=checked_table
           AND attribute.attname=field_name
           AND attribute.attnum > 0
           AND NOT attribute.attisdropped;
        -- SQLAlchemy's native Enum persists member names (for example
        -- ``SELL``), while the versioned application payload intentionally
        -- transports their public values (``sell``).  Only catalog-proven
        -- enum columns receive this normalization; ordinary text remains
        -- case-sensitive.
        IF enum_column
           AND jsonb_typeof(source_value)='string'
           AND jsonb_typeof(supplied_value)='string'
           AND lower(source_value #>> '{}')=lower(supplied_value #>> '{}') THEN
            CONTINUE;
        END IF;
        IF jsonb_typeof(source_value)='string'
           AND jsonb_typeof(supplied_value)='string' THEN
            IF column_type='timestamptz' THEN
                IF (source_value #>> '{}')::timestamptz =
                   (supplied_value #>> '{}')::timestamptz THEN CONTINUE; END IF;
            ELSIF column_type='timestamp' THEN
                IF (source_value #>> '{}')::timestamp =
                   (supplied_value #>> '{}')::timestamp THEN CONTINUE; END IF;
            ELSIF column_type='date' THEN
                IF (source_value #>> '{}')::date =
                   (supplied_value #>> '{}')::date THEN CONTINUE; END IF;
            ELSIF column_type='time' THEN
                IF (source_value #>> '{}')::time =
                   (supplied_value #>> '{}')::time THEN CONTINUE; END IF;
            ELSIF column_type='timetz' THEN
                IF (source_value #>> '{}')::timetz =
                   (supplied_value #>> '{}')::timetz THEN CONTINUE; END IF;
            END IF;
        END IF;
        RETURN false;
    END LOOP;
    RETURN true;
END;
$$;
"""


MUTATION_CAPTURE_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_capture_authoritative_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    state_row webapp_writer_state%ROWTYPE;
    row_json jsonb;
    pk_columns text[];
    column_name text;
    identity_values jsonb := '[]'::jsonb;
    identity_value jsonb;
    aggregate_db_identity text;
    required_authority text;
    required_writer_epoch bigint;
    capture_relation regclass;
    capture_owner oid;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'three-site database runtime singleton is missing';
    END IF;
    IF cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        RETURN NULL;
    END IF;
    capture_relation := to_regclass('pg_temp.trading_bot_authoritative_mutations');
    IF capture_relation IS NULL THEN
        CREATE TEMP TABLE trading_bot_authoritative_mutations (
            source_xid bigint NOT NULL,
            origin_authority text NOT NULL,
            origin_physical_site text NOT NULL,
            writer_epoch bigint,
            aggregate_type text NOT NULL,
            aggregate_db_id text NOT NULL,
            operation text NOT NULL,
            row_payload jsonb NOT NULL
        ) ON COMMIT DELETE ROWS;
        CREATE INDEX ON trading_bot_authoritative_mutations (
            source_xid, aggregate_type, aggregate_db_id, operation
        );
        REVOKE ALL ON TABLE trading_bot_authoritative_mutations FROM PUBLIC;
        capture_relation := to_regclass('pg_temp.trading_bot_authoritative_mutations');
    END IF;
    SELECT relation.relowner INTO capture_owner
      FROM pg_class relation
     WHERE relation.oid = capture_relation
       AND relation.relpersistence = 't'
       AND relation.relkind = 'r';
    IF capture_owner IS NULL OR capture_owner IS DISTINCT FROM current_user::regrole::oid THEN
        RAISE EXCEPTION 'authoritative mutation capture relation is not trusted';
    END IF;
    IF cfg.physical_site = 'bot_fi' THEN
        required_authority := 'foreign';
        required_writer_epoch := NULL;
    ELSIF cfg.physical_site IN ('webapp_fi','webapp_ir') THEN
        required_authority := 'webapp';
        SELECT * INTO state_row FROM webapp_writer_state WHERE authority = 'webapp' FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'three-site mutation capture writer state is missing'; END IF;
        required_writer_epoch := state_row.writer_epoch;
    ELSE
        RAISE EXCEPTION 'three-site mutation capture physical site is invalid';
    END IF;
    IF TG_OP = 'DELETE' THEN row_json := to_jsonb(OLD); ELSE row_json := to_jsonb(NEW); END IF;
    SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
      INTO pk_columns
      FROM pg_index index_definition
      CROSS JOIN LATERAL unnest(index_definition.indkey)
           WITH ORDINALITY AS key_column(attribute_number, ordinality)
      JOIN pg_attribute attribute
        ON attribute.attrelid = index_definition.indrelid
       AND attribute.attnum = key_column.attribute_number
     WHERE index_definition.indrelid = TG_RELID AND index_definition.indisprimary;
    IF pk_columns IS NULL THEN
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
        aggregate_db_identity := replace(identity_values::text, ', ', ',');
    END IF;
    INSERT INTO pg_temp.trading_bot_authoritative_mutations (
        source_xid, origin_authority, origin_physical_site, writer_epoch,
        aggregate_type, aggregate_db_id, operation, row_payload
    ) VALUES (
        txid_current(), required_authority, cfg.physical_site, required_writer_epoch,
        TG_TABLE_NAME, aggregate_db_identity, TG_OP, row_json
    );
    RETURN NULL;
END;
$$;
"""


EVENT_COVERAGE_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_require_same_transaction_dr_event() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    state_row webapp_writer_state%ROWTYPE;
    row_json jsonb;
    pk_columns text[];
    column_name text;
    identity_values jsonb := '[]'::jsonb;
    identity_value jsonb;
    aggregate_db_identity text;
    required_capability text;
    required_authority text;
    required_writer_epoch bigint;
    event_count bigint;
    mutation_count bigint;
    identity_event_count bigint;
    payload_event_count bigint;
    integrity_event_count bigint;
    diagnostic_payload jsonb;
    expected_payload_keys text[];
    supplied_payload_keys text[];
    mismatched_payload_fields text[];
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
    IF NOT FOUND THEN RAISE EXCEPTION 'three-site database runtime singleton is missing'; END IF;
    IF cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN RETURN NULL; END IF;
    IF cfg.physical_site = 'bot_fi' THEN
        required_capability := 'foreign_writer'; required_authority := 'foreign'; required_writer_epoch := NULL;
    ELSIF cfg.physical_site IN ('webapp_fi','webapp_ir') THEN
        required_capability := 'writer'; required_authority := 'webapp';
        SELECT * INTO state_row FROM webapp_writer_state WHERE authority='webapp' FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'three-site event coverage writer state is missing'; END IF;
        required_writer_epoch := state_row.writer_epoch;
    ELSE RAISE EXCEPTION 'three-site event coverage physical site is invalid'; END IF;
    IF current_setting('trading_bot.mutation_capability', true) IS DISTINCT FROM required_capability THEN
        RAISE EXCEPTION 'authoritative event coverage requires % capability', required_capability;
    END IF;
    IF current_setting('trading_bot.physical_site', true) IS DISTINCT FROM cfg.physical_site THEN
        RAISE EXCEPTION 'authoritative event coverage physical site is missing or stale';
    END IF;
    IF TG_OP='DELETE' THEN row_json := to_jsonb(OLD); ELSE row_json := to_jsonb(NEW); END IF;
    SELECT array_agg(attribute.attname ORDER BY key_column.ordinality) INTO pk_columns
      FROM pg_index index_definition
      CROSS JOIN LATERAL unnest(index_definition.indkey) WITH ORDINALITY AS key_column(attribute_number, ordinality)
      JOIN pg_attribute attribute ON attribute.attrelid=index_definition.indrelid AND attribute.attnum=key_column.attribute_number
     WHERE index_definition.indrelid=TG_RELID AND index_definition.indisprimary;
    IF pk_columns IS NULL THEN RAISE EXCEPTION 'authoritative table % lacks a primary key', TG_TABLE_NAME; END IF;
    FOREACH column_name IN ARRAY pk_columns LOOP
        identity_value := row_json -> column_name;
        IF identity_value IS NULL OR identity_value='null'::jsonb THEN
            RAISE EXCEPTION 'authoritative table % has an incomplete primary key', TG_TABLE_NAME;
        END IF;
        identity_values := identity_values || jsonb_build_array(identity_value);
    END LOOP;
    IF jsonb_array_length(identity_values)=1 THEN aggregate_db_identity := identity_values ->> 0;
    ELSE aggregate_db_identity := replace(identity_values::text, ', ', ','); END IF;

    SELECT count(*) INTO event_count FROM dr_events event
     WHERE event.source_xid=txid_current()
       AND event.aggregate_type=TG_TABLE_NAME
       AND event.aggregate_db_id=aggregate_db_identity
       AND event.operation=TG_OP
       AND event.origin_authority=required_authority
       AND event.origin_physical_site=cfg.physical_site
       AND event.writer_epoch IS NOT DISTINCT FROM required_writer_epoch
       AND event.tombstone IS NOT DISTINCT FROM (TG_OP='DELETE')
       AND trading_bot_projection_payload_matches(TG_TABLE_NAME, row_json, event.canonical_payload::jsonb)
       AND trading_bot_dr_event_integrity_valid(event.event_id);
    SELECT count(*) INTO identity_event_count FROM dr_events event
     WHERE event.source_xid=txid_current()
       AND event.aggregate_type=TG_TABLE_NAME
       AND event.aggregate_db_id=aggregate_db_identity
       AND event.operation=TG_OP
       AND event.origin_authority=required_authority
       AND event.origin_physical_site=cfg.physical_site
       AND event.writer_epoch IS NOT DISTINCT FROM required_writer_epoch;
    SELECT count(*) INTO payload_event_count FROM dr_events event
     WHERE event.source_xid=txid_current()
       AND event.aggregate_type=TG_TABLE_NAME
       AND event.aggregate_db_id=aggregate_db_identity
       AND event.operation=TG_OP
       AND trading_bot_projection_payload_matches(
           TG_TABLE_NAME, row_json, event.canonical_payload::jsonb
       );
    SELECT count(*) INTO integrity_event_count FROM dr_events event
     WHERE event.source_xid=txid_current()
       AND event.aggregate_type=TG_TABLE_NAME
       AND event.aggregate_db_id=aggregate_db_identity
       AND event.operation=TG_OP
       AND trading_bot_dr_event_integrity_valid(event.event_id);
    SELECT count(*) INTO mutation_count
      FROM pg_temp.trading_bot_authoritative_mutations mutation
     WHERE mutation.source_xid=txid_current()
       AND mutation.aggregate_type=TG_TABLE_NAME
       AND mutation.aggregate_db_id=aggregate_db_identity
       AND mutation.operation=TG_OP
       AND mutation.origin_authority=required_authority
       AND mutation.origin_physical_site=cfg.physical_site
       AND mutation.writer_epoch IS NOT DISTINCT FROM required_writer_epoch
       AND mutation.row_payload::jsonb IS NOT DISTINCT FROM row_json;
    IF mutation_count < 1 OR event_count IS DISTINCT FROM mutation_count THEN
        SELECT event.canonical_payload::jsonb INTO diagnostic_payload
          FROM dr_events event
         WHERE event.source_xid=txid_current()
           AND event.aggregate_type=TG_TABLE_NAME
           AND event.aggregate_db_id=aggregate_db_identity
           AND event.operation=TG_OP
         ORDER BY event.event_id LIMIT 1;
        SELECT array_agg(field.column_name ORDER BY field.column_name)
          INTO expected_payload_keys
          FROM dr_projection_field_allowlist field
         WHERE field.table_name=TG_TABLE_NAME;
        SELECT array_agg(field.key ORDER BY field.key)
          INTO supplied_payload_keys
          FROM jsonb_object_keys(COALESCE(diagnostic_payload, '{}'::jsonb)) field(key);
        SELECT array_agg(field.column_name ORDER BY field.column_name)
          INTO mismatched_payload_fields
          FROM dr_projection_field_allowlist field
         WHERE field.table_name=TG_TABLE_NAME
           AND row_json -> field.column_name IS DISTINCT FROM diagnostic_payload -> field.column_name
           AND NOT (
               jsonb_typeof(row_json -> field.column_name)='number'
               AND jsonb_typeof(diagnostic_payload -> field.column_name)='string'
               AND row_json -> field.column_name #>> '{}'
                   = diagnostic_payload -> field.column_name #>> '{}'
           );
        RAISE EXCEPTION 'authoritative mutation on %/% operation % has mutation %, exact event %, identity %, payload %, integrity %, expected keys %, supplied keys %, mismatched fields %',
            TG_TABLE_NAME, aggregate_db_identity, TG_OP, mutation_count, event_count,
            identity_event_count, payload_event_count, integrity_event_count,
            expected_payload_keys, supplied_payload_keys, mismatched_payload_fields;
    END IF;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_require_event_mutation_binding() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    event_row dr_events%ROWTYPE;
    event_count bigint;
    mutation_count bigint;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id=1;
    IF NOT FOUND OR cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        RETURN NULL;
    END IF;
    SELECT * INTO event_row FROM dr_events WHERE event_id=NEW.event_id;
    IF NOT FOUND OR event_row.source_xid IS DISTINCT FROM txid_current()
       OR NOT trading_bot_dr_event_integrity_valid(event_row.event_id) THEN
        RAISE EXCEPTION 'local DR event is not a finalized database-bound envelope';
    END IF;
    SELECT count(*) INTO event_count FROM dr_events event
     WHERE event.source_xid=event_row.source_xid
       AND event.aggregate_type=event_row.aggregate_type
       AND event.aggregate_db_id IS NOT DISTINCT FROM event_row.aggregate_db_id
       AND event.operation=event_row.operation
       AND event.origin_authority=event_row.origin_authority
       AND event.origin_physical_site=event_row.origin_physical_site
       AND event.writer_epoch IS NOT DISTINCT FROM event_row.writer_epoch
       AND event.canonical_payload::jsonb IS NOT DISTINCT FROM event_row.canonical_payload::jsonb
       AND trading_bot_dr_event_integrity_valid(event.event_id);
    IF to_regclass('pg_temp.trading_bot_authoritative_mutations') IS NULL THEN
        RAISE EXCEPTION 'local DR event has no authoritative mutation capture';
    END IF;
    SELECT count(*) INTO mutation_count
      FROM pg_temp.trading_bot_authoritative_mutations mutation
     WHERE mutation.source_xid=event_row.source_xid
       AND mutation.aggregate_type=event_row.aggregate_type
       AND mutation.aggregate_db_id IS NOT DISTINCT FROM event_row.aggregate_db_id
       AND mutation.operation=event_row.operation
       AND mutation.origin_authority=event_row.origin_authority
       AND mutation.origin_physical_site=event_row.origin_physical_site
       AND mutation.writer_epoch IS NOT DISTINCT FROM event_row.writer_epoch
       AND trading_bot_projection_payload_matches(
           event_row.aggregate_type, mutation.row_payload::jsonb,
           event_row.canonical_payload::jsonb
       );
    IF mutation_count < 1 OR event_count IS DISTINCT FROM mutation_count THEN
        RAISE EXCEPTION 'local DR event lacks an exact authoritative mutation binding';
    END IF;
    RETURN NULL;
END;
$$;
"""


CURSOR_GUARD_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_guard_local_dr_cursor() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    state_row webapp_writer_state%ROWTYPE;
    expected_authority text;
    expected_epoch bigint;
    expected_capability text;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id=1;
    IF NOT FOUND OR cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF cfg.physical_site='bot_fi' THEN
        expected_authority := 'foreign';
        expected_epoch := NULLIF(current_setting('trading_bot.dr_producer_epoch', true), '')::bigint;
        expected_capability := 'foreign_writer';
    ELSE
        expected_authority := 'webapp'; expected_capability := 'writer';
        SELECT * INTO state_row FROM webapp_writer_state WHERE authority='webapp' FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'DR cursor writer state is missing'; END IF;
        expected_epoch := state_row.writer_epoch;
    END IF;
    IF current_setting('trading_bot.mutation_capability', true) IS DISTINCT FROM expected_capability
       OR current_setting('trading_bot.physical_site', true) IS DISTINCT FROM cfg.physical_site THEN
        RAISE EXCEPTION 'DR cursor mutation lacks local writer capability';
    END IF;
    IF TG_OP='DELETE' THEN RAISE EXCEPTION 'DR cursors cannot be deleted by the application role'; END IF;
    IF NEW.origin_authority IS DISTINCT FROM expected_authority
       OR NEW.origin_physical_site IS DISTINCT FROM cfg.physical_site
       OR NEW.producer_epoch IS DISTINCT FROM expected_epoch THEN
        RAISE EXCEPTION 'DR cursor identity does not match the current writer term';
    END IF;
    IF TG_TABLE_NAME='dr_destination_cursors' THEN
        IF to_jsonb(NEW) ->> 'destination_site' NOT IN ('bot_fi','webapp_fi','webapp_ir')
           OR to_jsonb(NEW) ->> 'destination_site'=cfg.physical_site THEN
            RAISE EXCEPTION 'DR destination cursor site is invalid';
        END IF;
    END IF;
    IF TG_OP='INSERT' AND NEW.last_sequence IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'DR cursor must start at sequence one';
    ELSIF TG_OP='UPDATE' AND (
        NEW.last_sequence IS DISTINCT FROM OLD.last_sequence + 1
        OR NEW.origin_authority IS DISTINCT FROM OLD.origin_authority
        OR NEW.origin_physical_site IS DISTINCT FROM OLD.origin_physical_site
        OR NEW.producer_epoch IS DISTINCT FROM OLD.producer_epoch
        OR (TG_TABLE_NAME='dr_destination_cursors'
            AND to_jsonb(NEW) ->> 'destination_site'
                IS DISTINCT FROM to_jsonb(OLD) ->> 'destination_site')
    ) THEN
        RAISE EXCEPTION 'DR cursor may advance by exactly one only';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_bind_local_dr_event_sequences() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    supplied_destinations text[];
    required_destinations text[];
    destination text;
    stream_sequence bigint;
    cursor_sequence bigint;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id=1;
    IF NOT FOUND OR cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        RETURN NULL;
    END IF;
    IF NEW.source_xid IS DISTINCT FROM txid_current() THEN
        RAISE EXCEPTION 'local DR event source transaction is not database-bound';
    END IF;
    SELECT COALESCE(array_agg(destination.key ORDER BY destination.key), ARRAY[]::text[])
      INTO supplied_destinations
      FROM jsonb_object_keys(NEW.destination_streams::jsonb) AS destination(key);
    SELECT COALESCE(array_agg(destination.site ORDER BY destination.site), ARRAY[]::text[])
      INTO required_destinations
      FROM unnest(trading_bot_required_dr_destinations(
          NEW.origin_physical_site, NEW.aggregate_type
      )) AS destination(site);
    IF supplied_destinations <> required_destinations THEN
        RAISE EXCEPTION 'local DR event destination entitlement is incomplete or excessive';
    END IF;
    SELECT last_sequence INTO cursor_sequence FROM dr_producer_cursors
     WHERE origin_authority=NEW.origin_authority
       AND origin_physical_site=NEW.origin_physical_site
       AND producer_epoch=NEW.producer_epoch;
    IF cursor_sequence IS DISTINCT FROM NEW.producer_sequence THEN
        RAISE EXCEPTION 'local DR event producer sequence is not cursor allocated';
    END IF;
    FOREACH destination IN ARRAY supplied_destinations LOOP
        stream_sequence := (NEW.destination_streams::jsonb -> destination ->> 'sequence')::bigint;
        SELECT last_sequence INTO cursor_sequence FROM dr_destination_cursors
         WHERE origin_authority=NEW.origin_authority
           AND origin_physical_site=NEW.origin_physical_site
           AND producer_epoch=NEW.producer_epoch
           AND destination_site=destination;
        IF cursor_sequence IS DISTINCT FROM stream_sequence THEN
            RAISE EXCEPTION 'local DR destination sequence is not cursor allocated';
        END IF;
        INSERT INTO dr_event_destination_sequences (
            event_id, origin_authority, origin_physical_site, producer_epoch,
            destination_site, destination_sequence
        ) VALUES (
            NEW.event_id, NEW.origin_authority, NEW.origin_physical_site,
            NEW.producer_epoch, destination, stream_sequence
        );
    END LOOP;
    RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_require_cursor_event_tail() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    matching_rows bigint;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id=1;
    IF NOT FOUND OR cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        RETURN NULL;
    END IF;
    IF TG_TABLE_NAME='dr_producer_cursors' THEN
        -- Validate every cursor transition independently.  Comparing only the
        -- final max permits N and N+1 to be allocated in one transaction while
        -- emitting only N+1, permanently creating an accepted stream gap.
        SELECT count(*) INTO matching_rows FROM dr_events event
         WHERE event.source_xid IS NOT NULL
           AND event.source_xid=txid_current()
           AND event.origin_authority=NEW.origin_authority
           AND event.origin_physical_site=NEW.origin_physical_site
           AND event.producer_epoch=NEW.producer_epoch
           AND event.producer_sequence=NEW.last_sequence;
    ELSE
        SELECT count(*) INTO matching_rows
          FROM dr_event_destination_sequences binding
          JOIN dr_events event ON event.event_id=binding.event_id
         WHERE binding.origin_authority=NEW.origin_authority
           AND binding.origin_physical_site=NEW.origin_physical_site
           AND binding.producer_epoch=NEW.producer_epoch
           AND binding.destination_site=NEW.destination_site
           AND binding.destination_sequence=NEW.last_sequence
           AND event.source_xid=txid_current();
    END IF;
    IF matching_rows IS DISTINCT FROM 1::bigint THEN
        RAISE EXCEPTION 'each DR cursor transition must bind exactly one same-transaction event sequence';
    END IF;
    RETURN NULL;
END;
$$;
"""


DR_EVENT_IMMUTABILITY_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_dr_event_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    old_destination_sequences jsonb;
    new_destination_sequences jsonb;
BEGIN
    IF TG_OP = 'UPDATE'
       AND OLD.protocol_version = 2
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
       AND jsonb_typeof(OLD.destination_streams::jsonb) = 'object'
       AND jsonb_typeof(NEW.destination_streams::jsonb) = 'object'
       AND NEW.destination_streams::jsonb <> '{}'::jsonb THEN
        SELECT jsonb_object_agg(stream.key, stream.value -> 'sequence')
          INTO old_destination_sequences
          FROM jsonb_each(OLD.destination_streams::jsonb) AS stream(key, value);
        SELECT jsonb_object_agg(stream.key, stream.value -> 'sequence')
          INTO new_destination_sequences
          FROM jsonb_each(NEW.destination_streams::jsonb) AS stream(key, value);
        IF new_destination_sequences IS NOT DISTINCT FROM old_destination_sequences THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'dr_events are immutable';
END;
$$;
"""


LOCAL_DESTINATION_BINDING_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_local_dr_destination_binding_valid(
    checked_event_id text
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    event_row dr_events%ROWTYPE;
    destination record;
    stream_fields text[];
    stream_sequence bigint;
    stream_count bigint;
    binding_count bigint;
BEGIN
    SELECT * INTO event_row FROM dr_events WHERE event_id=checked_event_id;
    IF NOT FOUND THEN RETURN false; END IF;
    -- Receiver/projector events are remote and intentionally have no local
    -- allocation binding.  Only database-bound local events are constrained.
    IF event_row.source_xid IS NULL THEN RETURN true; END IF;
    IF event_row.destination_streams IS NULL
       OR jsonb_typeof(event_row.destination_streams::jsonb) <> 'object'
       OR event_row.destination_streams::jsonb = '{}'::jsonb THEN
        RETURN false;
    END IF;
    SELECT count(*) INTO stream_count
      FROM jsonb_each(event_row.destination_streams::jsonb);
    SELECT count(*) INTO binding_count
      FROM dr_event_destination_sequences binding
     WHERE binding.event_id=event_row.event_id;
    IF binding_count IS DISTINCT FROM stream_count THEN RETURN false; END IF;
    FOR destination IN
        SELECT key AS site, value AS stream
          FROM jsonb_each(event_row.destination_streams::jsonb)
    LOOP
        IF jsonb_typeof(destination.stream) <> 'object' THEN RETURN false; END IF;
        SELECT array_agg(field.key ORDER BY field.key) INTO stream_fields
          FROM jsonb_object_keys(destination.stream) AS field(key);
        IF stream_fields IS DISTINCT FROM ARRAY[
            'sequence','transaction_hash','transaction_id',
            'transaction_position','transaction_size'
        ]::text[] THEN
            RETURN false;
        END IF;
        stream_sequence := (destination.stream ->> 'sequence')::bigint;
        SELECT count(*) INTO binding_count
          FROM dr_event_destination_sequences binding
         WHERE binding.event_id=event_row.event_id
           AND binding.destination_site=destination.site
           AND binding.origin_authority=event_row.origin_authority
           AND binding.origin_physical_site=event_row.origin_physical_site
           AND binding.producer_epoch=event_row.producer_epoch
           AND binding.destination_sequence=stream_sequence;
        IF binding_count IS DISTINCT FROM 1::bigint THEN RETURN false; END IF;
    END LOOP;
    RETURN true;
EXCEPTION
    WHEN data_exception OR invalid_text_representation THEN
        RETURN false;
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_require_local_dr_destination_binding()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    event_source_xid bigint;
BEGIN
    SELECT source_xid INTO event_source_xid FROM dr_events WHERE event_id=NEW.event_id;
    IF event_source_xid IS NULL THEN RETURN NULL; END IF;
    IF NOT trading_bot_local_dr_destination_binding_valid(NEW.event_id) THEN
        RAISE EXCEPTION 'local DR event destination streams differ from allocated bindings';
    END IF;
    RETURN NULL;
END;
$$;
"""


NONCE_CLEANUP_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_cleanup_expired_replay_nonces(
    cutoff timestamptz,
    row_limit integer
) RETURNS TABLE(key_id text, nonce text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    safe_cutoff timestamptz;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
    IF NOT FOUND
       OR current_setting('trading_bot.mutation_capability', true) IS DISTINCT FROM 'projection'
       OR current_setting('trading_bot.projection_scope', true) IS DISTINCT FROM 'projector'
       OR NOT EXISTS (
            SELECT 1 FROM dr_projection_service_roles service_role
             WHERE service_role.physical_site = cfg.physical_site
               AND service_role.service_scope = 'projector'
               AND service_role.database_role = session_user
       ) THEN
        RAISE EXCEPTION 'DR replay nonce cleanup requires the bound projector role';
    END IF;
    IF cutoff IS NULL OR row_limit IS NULL OR row_limit < 1 OR row_limit > 500 THEN
        RAISE EXCEPTION 'DR replay nonce cleanup arguments are invalid';
    END IF;
    -- Never trust the caller to move retention into the future.  Five minutes
    -- is the project minimum/default replay-retention window; a configured
    -- longer window remains honored because its supplied cutoff is older.
    safe_cutoff := LEAST(cutoff, clock_timestamp() - interval '5 minutes');
    RETURN QUERY
        DELETE FROM dr_replay_nonces target
         WHERE target.ctid IN (
            SELECT candidate.ctid FROM dr_replay_nonces candidate
             WHERE candidate.expires_at < safe_cutoff
             ORDER BY candidate.expires_at, candidate.key_id, candidate.nonce
             FOR UPDATE SKIP LOCKED LIMIT row_limit
         )
         RETURNING target.key_id::text, target.nonce::text;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "dr_event_destination_sequences",
        sa.Column("event_id", sa.String(length=36), sa.ForeignKey("dr_events.event_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("destination_site", sa.String(length=16), primary_key=True),
        sa.Column("origin_authority", sa.String(length=16), nullable=False),
        sa.Column("origin_physical_site", sa.String(length=16), nullable=False),
        sa.Column("producer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("destination_sequence", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("destination_sequence >= 1", name="ck_dr_event_destination_sequence_positive"),
        sa.CheckConstraint("destination_site IN ('bot_fi','webapp_fi','webapp_ir')", name="ck_dr_event_destination_sequence_site"),
        sa.UniqueConstraint(
            "origin_authority", "origin_physical_site", "producer_epoch",
            "destination_site", "destination_sequence",
            name="ux_dr_event_destination_sequence_stream",
        ),
    )
    op.execute(
        "INSERT INTO dr_event_destination_sequences ("
        "event_id, destination_site, origin_authority, origin_physical_site, "
        "producer_epoch, destination_sequence) "
        "SELECT event.event_id, stream.key, event.origin_authority, "
        "event.origin_physical_site, event.producer_epoch, "
        "(stream.value ->> 'sequence')::bigint "
        "FROM dr_events event CROSS JOIN LATERAL "
        "jsonb_each(event.destination_streams::jsonb) stream "
        "WHERE event.source_xid IS NOT NULL AND event.protocol_version=2"
    )

    op.execute(DR_EVENT_IMMUTABILITY_SQL)
    op.execute(LOCAL_DESTINATION_BINDING_SQL)
    op.execute(REQUIRED_DESTINATIONS_SQL)
    op.execute(PAYLOAD_MATCH_SQL)
    op.execute(
        "ALTER FUNCTION trading_bot_dr_event_integrity_valid(text) "
        "RENAME TO trading_bot_dr_event_payload_integrity_valid"
    )
    op.execute(
        "CREATE FUNCTION trading_bot_dr_event_integrity_valid(checked_event_id text) "
        "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path=public,pg_temp AS $$ SELECT "
        "trading_bot_dr_event_payload_integrity_valid(checked_event_id) AND "
        "trading_bot_dr_event_entitlement_valid(checked_event_id) AND "
        "trading_bot_local_dr_destination_binding_valid(checked_event_id) $$"
    )
    op.execute(MUTATION_CAPTURE_SQL)
    op.execute(EVENT_COVERAGE_SQL)
    op.execute(CURSOR_GUARD_SQL)
    op.execute(NONCE_CLEANUP_SQL)

    for table in EVENT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_three_site_mutation_capture ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_three_site_mutation_capture "
            f"AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
            "EXECUTE FUNCTION trading_bot_capture_authoritative_mutation()"
        )
    for table in ("dr_producer_cursors", "dr_destination_cursors"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_three_site_cursor_guard ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_three_site_cursor_guard BEFORE INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION trading_bot_guard_local_dr_cursor()"
        )
        op.execute(f"DROP TRIGGER IF EXISTS trg_three_site_cursor_tail ON {table}")
        op.execute(
            f"CREATE CONSTRAINT TRIGGER trg_three_site_cursor_tail AFTER INSERT OR UPDATE "
            f"ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
            "EXECUTE FUNCTION trading_bot_require_cursor_event_tail()"
        )
    op.execute("DROP TRIGGER IF EXISTS trg_dr_bind_local_sequences ON dr_events")
    op.execute(
        "CREATE TRIGGER trg_dr_bind_local_sequences AFTER INSERT ON dr_events "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_bind_local_dr_event_sequences()"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_dr_event_destination_binding ON dr_events")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_dr_event_destination_binding "
        "AFTER INSERT OR UPDATE ON dr_events DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_require_local_dr_destination_binding()"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_dr_event_mutation_binding ON dr_events")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_dr_event_mutation_binding "
        "AFTER INSERT OR UPDATE ON dr_events DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_require_event_mutation_binding()"
    )

    # Replica-mode defaults must never suppress a security boundary.  The
    # trigger functions retain explicit session-role/scope checks so genuine
    # receiver/projector work remains separated while every trigger fires.
    op.execute(
        "DO $$ DECLARE item record; BEGIN FOR item IN "
        "SELECT relation.oid::regclass AS relation_name, trigger.tgname "
        "FROM pg_trigger trigger JOIN pg_class relation ON relation.oid=trigger.tgrelid "
        "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
        "WHERE namespace.nspname='public' AND NOT trigger.tgisinternal "
        "AND trigger.tgname IN ("
        "'trg_three_site_writer_term','trg_three_site_event_coverage',"
        "'trg_three_site_mutation_capture','trg_three_site_cursor_guard',"
        "'trg_three_site_cursor_tail','trg_dr_events_immutable',"
        "'trg_dr_event_finalized','trg_dr_receiver_source_xid',"
        "'trg_dr_bind_local_sequences','trg_dr_event_destination_binding',"
        "'trg_dr_event_mutation_binding',"
        "'trg_dr_effect_intent_immutable','trg_dr_effect_fanout_intent_immutable') "
        "LOOP EXECUTE format('ALTER TABLE %s ENABLE ALWAYS TRIGGER %I', "
        "item.relation_name, item.tgname); END LOOP; END $$"
    )

    for function_identity in (
        "trading_bot_required_dr_destinations(text, text)",
        "trading_bot_dr_event_entitlement_valid(text)",
        "trading_bot_dr_event_immutable()",
        "trading_bot_local_dr_destination_binding_valid(text)",
        "trading_bot_dr_event_payload_integrity_valid(text)",
        "trading_bot_dr_event_integrity_valid(text)",
        "trading_bot_projection_payload_matches(text, jsonb, jsonb)",
        "trading_bot_capture_authoritative_mutation()",
        "trading_bot_require_same_transaction_dr_event()",
        "trading_bot_require_event_mutation_binding()",
        "trading_bot_guard_local_dr_cursor()",
        "trading_bot_bind_local_dr_event_sequences()",
        "trading_bot_require_local_dr_destination_binding()",
        "trading_bot_require_cursor_event_tail()",
        "trading_bot_cleanup_expired_replay_nonces(timestamptz, integer)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function_identity} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "e653f4a5b7c8 is a forward-only security migration; use the reviewed restore/forward-rollback runbook"
    )
