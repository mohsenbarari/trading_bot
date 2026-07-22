"""Seal the DR destination-stream and migration-history contract.

Revision ID: f764a5b6c8d9
Revises: e653f4a5b7c8
"""

from __future__ import annotations

from alembic import op


revision = "f764a5b6c8d9"
down_revision = "e653f4a5b7c8"
branch_labels = None
depends_on = None


STRICT_DESTINATION_SCHEMA_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_dr_json_positive_integer(
    value jsonb,
    maximum_value bigint
) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER
SET search_path = public, pg_temp AS $$
DECLARE
    rendered text;
    maximum_rendered text;
BEGIN
    IF maximum_value < 1 OR jsonb_typeof(value) <> 'number' THEN
        RETURN false;
    END IF;
    rendered := value::text;
    maximum_rendered := maximum_value::text;
    -- JSONB preserves a fractional scale (for example 1.0) and renders
    -- exponent input as a decimal.  Requiring decimal digits only therefore
    -- matches Python's exact-int protocol rule instead of PostgreSQL's broad
    -- text-to-integer coercions.
    IF rendered !~ '^[1-9][0-9]*$' THEN
        RETURN false;
    END IF;
    RETURN length(rendered) < length(maximum_rendered)
        OR (length(rendered) = length(maximum_rendered)
            AND rendered <= maximum_rendered);
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_dr_destination_schema_valid(
    checked_event_id text
) RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    event_row dr_events%ROWTYPE;
    destination record;
    stream_fields text[];
    stream_position integer;
    stream_size integer;
BEGIN
    SELECT * INTO event_row FROM dr_events WHERE event_id=checked_event_id;
    IF NOT FOUND
       OR event_row.protocol_version IS DISTINCT FROM 2
       OR event_row.destination_streams IS NULL
       OR jsonb_typeof(event_row.destination_streams::jsonb) <> 'object'
       OR event_row.destination_streams::jsonb = '{}'::jsonb THEN
        RETURN false;
    END IF;
    FOR destination IN
        SELECT key AS site, value AS stream
          FROM jsonb_each(event_row.destination_streams::jsonb)
    LOOP
        IF destination.site NOT IN ('bot_fi','webapp_fi','webapp_ir')
           OR destination.site = event_row.origin_physical_site
           OR jsonb_typeof(destination.stream) <> 'object' THEN
            RETURN false;
        END IF;
        SELECT array_agg(field.key ORDER BY field.key) INTO stream_fields
          FROM jsonb_object_keys(destination.stream) AS field(key);
        IF stream_fields IS DISTINCT FROM ARRAY[
            'sequence','transaction_hash','transaction_id',
            'transaction_position','transaction_size'
        ]::text[]
           OR trading_bot_dr_json_positive_integer(
                  destination.stream -> 'sequence', 9223372036854775807
              ) IS NOT TRUE
           OR trading_bot_dr_json_positive_integer(
                  destination.stream -> 'transaction_position', 2147483647
              ) IS NOT TRUE
           OR trading_bot_dr_json_positive_integer(
                  destination.stream -> 'transaction_size', 2147483647
              ) IS NOT TRUE
           OR jsonb_typeof(destination.stream -> 'transaction_id') <> 'string'
           OR destination.stream ->> 'transaction_id'
                IS DISTINCT FROM event_row.transaction_id
           OR jsonb_typeof(destination.stream -> 'transaction_hash') <> 'string'
           OR destination.stream ->> 'transaction_hash' !~ '^[0-9a-f]{64}$'
           OR destination.stream ->> 'transaction_hash' = repeat('0', 64) THEN
            RETURN false;
        END IF;
        stream_position := (destination.stream ->> 'transaction_position')::integer;
        stream_size := (destination.stream ->> 'transaction_size')::integer;
        IF stream_position > stream_size THEN
            RETURN false;
        END IF;
    END LOOP;
    RETURN true;
EXCEPTION
    WHEN data_exception OR invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN false;
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
    stream_sequence bigint;
    stream_count bigint;
    binding_count bigint;
BEGIN
    SELECT * INTO event_row FROM dr_events WHERE event_id=checked_event_id;
    IF NOT FOUND THEN RETURN false; END IF;
    -- Remote receiver/projector events have no local allocation binding.
    IF event_row.source_xid IS NULL THEN RETURN true; END IF;
    IF trading_bot_dr_destination_schema_valid(event_row.event_id) IS NOT TRUE THEN
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
    WHEN data_exception OR invalid_text_representation OR numeric_value_out_of_range THEN
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
    IF trading_bot_local_dr_destination_binding_valid(NEW.event_id) IS NOT TRUE THEN
        RAISE EXCEPTION 'local DR event destination streams differ from allocated bindings';
    END IF;
    RETURN NULL;
END;
$$;
"""


CURSOR_FUNCTIONS_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_bind_local_dr_event_sequences() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    supplied_destinations text[];
    required_destinations text[];
    destination text;
    stream_sequence bigint;
    cursor_sequence bigint;
    stream jsonb;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id=1;
    IF NOT FOUND OR cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        RETURN NULL;
    END IF;
    IF NEW.source_xid IS DISTINCT FROM txid_current() THEN
        RAISE EXCEPTION 'local DR event source transaction is not database-bound';
    END IF;
    SELECT COALESCE(array_agg(item.key ORDER BY item.key), ARRAY[]::text[])
      INTO supplied_destinations
      FROM jsonb_object_keys(NEW.destination_streams::jsonb) AS item(key);
    SELECT COALESCE(array_agg(item.site ORDER BY item.site), ARRAY[]::text[])
      INTO required_destinations
      FROM unnest(trading_bot_required_dr_destinations(
          NEW.origin_physical_site, NEW.aggregate_type
      )) AS item(site);
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
        stream := NEW.destination_streams::jsonb -> destination;
        IF jsonb_typeof(stream) <> 'object'
           OR trading_bot_dr_json_positive_integer(
                  stream -> 'sequence', 9223372036854775807
              ) IS NOT TRUE THEN
            RAISE EXCEPTION 'local DR destination sequence must be a canonical positive JSON integer';
        END IF;
        stream_sequence := (stream ->> 'sequence')::bigint;
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
        SELECT count(*) INTO matching_rows FROM dr_events event
         WHERE event.source_xid=txid_current()
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


HISTORY_PREFLIGHT_SQL = r"""
DO $$
DECLARE
    bad_event_id text;
    bad_stream text;
BEGIN
    SELECT event.event_id INTO bad_event_id
      FROM dr_events event
     WHERE event.source_xid IS NOT NULL
       AND event.protocol_version=2
       AND (
           trading_bot_dr_event_payload_integrity_valid(event.event_id) IS NOT TRUE
           OR trading_bot_dr_event_entitlement_valid(event.event_id) IS NOT TRUE
           OR trading_bot_dr_destination_schema_valid(event.event_id) IS NOT TRUE
           OR trading_bot_local_dr_destination_binding_valid(event.event_id) IS NOT TRUE
       )
     ORDER BY event.event_id
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight rejected local event %', bad_event_id
          USING DETAIL = 'payload, entitlement, canonical JSON scalar type, or destination binding is invalid';
    END IF;

    SELECT format('%s/%s/%s producer', event.origin_authority,
                  event.origin_physical_site, event.producer_epoch)
      INTO bad_stream
      FROM dr_events event
      LEFT JOIN dr_producer_cursors cursor
        ON cursor.origin_authority=event.origin_authority
       AND cursor.origin_physical_site=event.origin_physical_site
       AND cursor.producer_epoch=event.producer_epoch
     WHERE event.source_xid IS NOT NULL AND event.protocol_version=2
     GROUP BY event.origin_authority, event.origin_physical_site,
              event.producer_epoch, cursor.last_sequence
    HAVING min(event.producer_sequence) <> 1
        OR max(event.producer_sequence) <> count(*)
        OR count(DISTINCT event.producer_sequence) <> count(*)
        OR cursor.last_sequence IS DISTINCT FROM max(event.producer_sequence)
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight found a non-contiguous % stream', bad_stream;
    END IF;

    SELECT format('%s/%s/%s/%s destination', binding.origin_authority,
                  binding.origin_physical_site, binding.producer_epoch,
                  binding.destination_site)
      INTO bad_stream
      FROM dr_event_destination_sequences binding
      JOIN dr_events event ON event.event_id=binding.event_id
      LEFT JOIN dr_destination_cursors cursor
        ON cursor.origin_authority=binding.origin_authority
       AND cursor.origin_physical_site=binding.origin_physical_site
       AND cursor.producer_epoch=binding.producer_epoch
       AND cursor.destination_site=binding.destination_site
     WHERE event.source_xid IS NOT NULL AND event.protocol_version=2
     GROUP BY binding.origin_authority, binding.origin_physical_site,
              binding.producer_epoch, binding.destination_site,
              cursor.last_sequence
    HAVING min(binding.destination_sequence) <> 1
        OR max(binding.destination_sequence) <> count(*)
        OR count(DISTINCT binding.destination_sequence) <> count(*)
        OR cursor.last_sequence IS DISTINCT FROM max(binding.destination_sequence)
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight found a non-contiguous % stream', bad_stream;
    END IF;
END;
$$;
"""


def upgrade() -> None:
    op.execute(STRICT_DESTINATION_SCHEMA_SQL)
    op.execute(DR_EVENT_IMMUTABILITY_SQL)
    op.execute(LOCAL_DESTINATION_BINDING_SQL)
    op.execute(CURSOR_FUNCTIONS_SQL)
    op.execute(
        "CREATE OR REPLACE FUNCTION trading_bot_dr_event_integrity_valid(checked_event_id text) "
        "RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER "
        "SET search_path=public,pg_temp AS $$ SELECT "
        "trading_bot_dr_event_payload_integrity_valid(checked_event_id) AND "
        "trading_bot_dr_event_entitlement_valid(checked_event_id) AND "
        "trading_bot_dr_destination_schema_valid(checked_event_id) AND "
        "trading_bot_local_dr_destination_binding_valid(checked_event_id) $$"
    )
    op.execute(HISTORY_PREFLIGHT_SQL)

    op.execute("DROP TRIGGER IF EXISTS trg_dr_event_destination_binding ON dr_events")
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_dr_event_destination_binding "
        "AFTER INSERT OR UPDATE ON dr_events DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_require_local_dr_destination_binding()"
    )
    op.execute(
        "DO $$ DECLARE item record; BEGIN FOR item IN "
        "SELECT relation.oid::regclass AS relation_name, trigger.tgname "
        "FROM pg_trigger trigger JOIN pg_class relation ON relation.oid=trigger.tgrelid "
        "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
        "WHERE namespace.nspname='public' AND NOT trigger.tgisinternal "
        "AND trigger.tgname IN ("
        "'trg_three_site_cursor_tail','trg_dr_events_immutable',"
        "'trg_dr_bind_local_sequences','trg_dr_event_destination_binding',"
        "'trg_dr_event_mutation_binding') "
        "LOOP EXECUTE format('ALTER TABLE %s ENABLE ALWAYS TRIGGER %I', "
        "item.relation_name, item.tgname); END LOOP; END $$"
    )

    for function_identity in (
        "trading_bot_dr_json_positive_integer(jsonb, bigint)",
        "trading_bot_dr_destination_schema_valid(text)",
        "trading_bot_dr_event_immutable()",
        "trading_bot_local_dr_destination_binding_valid(text)",
        "trading_bot_require_local_dr_destination_binding()",
        "trading_bot_bind_local_dr_event_sequences()",
        "trading_bot_require_cursor_event_tail()",
        "trading_bot_dr_event_integrity_valid(text)",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function_identity} FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "f764a5b6c8d9 is a forward-only security migration; use the reviewed restore/forward-rollback runbook"
    )
