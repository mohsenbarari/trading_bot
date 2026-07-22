"""Harden DR event integrity and projection role boundaries.

Revision ID: d542e3f4a6b7
Revises: c431d2e3f5a6
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "d542e3f4a6b7"
down_revision = "c431d2e3f5a6"
branch_labels = None
depends_on = None


CANONICAL_JSON_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_canonical_jsonb(value jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    kind text := jsonb_typeof(value);
    rendered text;
BEGIN
    IF kind = 'object' THEN
        SELECT '{' || COALESCE(
            string_agg(to_jsonb(entry.key)::text || ':' || trading_bot_canonical_jsonb(entry.value), ',' ORDER BY entry.key),
            ''
        ) || '}' INTO rendered
          FROM jsonb_each(value) AS entry(key, value);
        RETURN rendered;
    ELSIF kind = 'array' THEN
        SELECT '[' || COALESCE(
            string_agg(trading_bot_canonical_jsonb(entry.value), ',' ORDER BY entry.ordinality),
            ''
        ) || ']' INTO rendered
          FROM jsonb_array_elements(value) WITH ORDINALITY AS entry(value, ordinality);
        RETURN rendered;
    END IF;
    RETURN value::text;
END;
$$;

CREATE OR REPLACE FUNCTION trading_bot_sha256_jsonb(value jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER SET search_path = public, pg_temp AS $$
    SELECT encode(sha256(convert_to(trading_bot_canonical_jsonb(value), 'UTF8')), 'hex')
$$;
"""


EVENT_INTEGRITY_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_dr_event_integrity_valid(checked_event_id text)
RETURNS boolean
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    event_row dr_events%ROWTYPE;
    transaction_members jsonb;
    destination_members jsonb;
    event_envelope jsonb;
    expected_transaction_hash text;
    expected_destination_hash text;
    destination text;
    destination_stream jsonb;
    member_count integer;
    distinct_positions integer;
    minimum_position integer;
    maximum_position integer;
    created_at_text text;
BEGIN
    SELECT * INTO event_row FROM dr_events WHERE event_id = checked_event_id;
    IF NOT FOUND
       OR event_row.protocol_version IS DISTINCT FROM 2
       OR event_row.source_xid IS NULL
       OR event_row.transaction_id IS NULL
       OR event_row.transaction_position IS NULL
       OR event_row.transaction_size IS NULL
       OR event_row.transaction_size < 1
       OR event_row.transaction_hash IS NULL
       OR event_row.destination_streams IS NULL
       OR jsonb_typeof(event_row.destination_streams::jsonb) <> 'object'
       OR event_row.canonical_payload_hash IS DISTINCT FROM
          trading_bot_sha256_jsonb(event_row.canonical_payload::jsonb) THEN
        RETURN false;
    END IF;

    SELECT
        jsonb_agg(
            jsonb_build_object(
                'event_id', member.event_id,
                'producer_sequence', member.producer_sequence,
                'transaction_position', member.transaction_position,
                'aggregate_type', member.aggregate_type,
                'aggregate_id', member.aggregate_id,
                'aggregate_db_id', member.aggregate_db_id,
                'aggregate_version', member.aggregate_version,
                'operation', member.operation,
                'canonical_payload_hash', member.canonical_payload_hash,
                'schema_version', member.schema_version,
                'writer_epoch', member.writer_epoch,
                'tombstone', member.tombstone
            ) ORDER BY member.transaction_position
        ),
        count(*), count(DISTINCT member.transaction_position),
        min(member.transaction_position), max(member.transaction_position)
      INTO transaction_members, member_count, distinct_positions,
           minimum_position, maximum_position
      FROM dr_events member
     WHERE member.origin_physical_site = event_row.origin_physical_site
       AND member.producer_epoch = event_row.producer_epoch
       AND member.transaction_id = event_row.transaction_id;

    IF member_count IS DISTINCT FROM event_row.transaction_size
       OR distinct_positions IS DISTINCT FROM member_count
       OR minimum_position IS DISTINCT FROM 1
       OR maximum_position IS DISTINCT FROM member_count THEN
        RETURN false;
    END IF;
    expected_transaction_hash := trading_bot_sha256_jsonb(transaction_members);
    IF event_row.transaction_hash IS DISTINCT FROM expected_transaction_hash THEN
        RETURN false;
    END IF;

    FOR destination IN
        SELECT jsonb_object_keys(event_row.destination_streams::jsonb)
    LOOP
        destination_stream := event_row.destination_streams::jsonb -> destination;
        IF jsonb_typeof(destination_stream) <> 'object'
           OR destination_stream ->> 'transaction_id' IS DISTINCT FROM event_row.transaction_id THEN
            RETURN false;
        END IF;

        SELECT
            jsonb_agg(
                jsonb_build_object(
                    'event_id', member.event_id,
                    'producer_sequence', member.producer_sequence,
                    'transaction_position',
                        (member.destination_streams::jsonb -> destination ->> 'transaction_position')::integer,
                    'aggregate_type', member.aggregate_type,
                    'aggregate_id', member.aggregate_id,
                    'aggregate_db_id', member.aggregate_db_id,
                    'aggregate_version', member.aggregate_version,
                    'operation', member.operation,
                    'canonical_payload_hash', member.canonical_payload_hash,
                    'schema_version', member.schema_version,
                    'writer_epoch', member.writer_epoch,
                    'tombstone', member.tombstone
                ) ORDER BY
                    (member.destination_streams::jsonb -> destination ->> 'transaction_position')::integer
            ),
            count(*),
            count(DISTINCT (member.destination_streams::jsonb -> destination ->> 'transaction_position')::integer),
            min((member.destination_streams::jsonb -> destination ->> 'transaction_position')::integer),
            max((member.destination_streams::jsonb -> destination ->> 'transaction_position')::integer)
          INTO destination_members, member_count, distinct_positions,
               minimum_position, maximum_position
          FROM dr_events member
         WHERE member.origin_physical_site = event_row.origin_physical_site
           AND member.producer_epoch = event_row.producer_epoch
           AND member.transaction_id = event_row.transaction_id
           AND member.destination_streams::jsonb ? destination;

        IF member_count < 1
           OR distinct_positions IS DISTINCT FROM member_count
           OR minimum_position IS DISTINCT FROM 1
           OR maximum_position IS DISTINCT FROM member_count
           OR (destination_stream ->> 'transaction_position')::integer < 1
           OR (destination_stream ->> 'transaction_position')::integer > member_count
           OR (destination_stream ->> 'transaction_size')::integer IS DISTINCT FROM member_count THEN
            RETURN false;
        END IF;
        expected_destination_hash := trading_bot_sha256_jsonb(destination_members);
        IF destination_stream ->> 'transaction_hash' IS DISTINCT FROM expected_destination_hash THEN
            RETURN false;
        END IF;
    END LOOP;

    created_at_text := to_char(
        event_row.created_at AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US'
    ) || '+00:00';
    event_envelope := jsonb_build_object(
        'protocol_version', event_row.protocol_version,
        'event_id', event_row.event_id,
        'origin_authority', event_row.origin_authority,
        'origin_physical_site', event_row.origin_physical_site,
        'producer_epoch', event_row.producer_epoch,
        'producer_sequence', event_row.producer_sequence,
        'aggregate_type', event_row.aggregate_type,
        'aggregate_id', event_row.aggregate_id,
        'aggregate_db_id', event_row.aggregate_db_id,
        'aggregate_version', event_row.aggregate_version,
        'operation', event_row.operation,
        'canonical_payload', event_row.canonical_payload::jsonb,
        'canonical_payload_hash', event_row.canonical_payload_hash,
        'schema_version', event_row.schema_version,
        'causation_id', event_row.causation_id,
        'idempotency_key', event_row.idempotency_key,
        'writer_epoch', event_row.writer_epoch,
        'tombstone', event_row.tombstone,
        'created_at', created_at_text,
        'transaction_id', event_row.transaction_id,
        'transaction_position', event_row.transaction_position,
        'transaction_size', event_row.transaction_size,
        'transaction_hash', event_row.transaction_hash,
        'destination_streams', event_row.destination_streams::jsonb
    );
    RETURN event_row.envelope_hash IS NOT DISTINCT FROM
        trading_bot_sha256_jsonb(event_envelope);
EXCEPTION
    WHEN data_exception OR invalid_text_representation OR numeric_value_out_of_range THEN
        RETURN false;
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
    RETURN QUERY
        DELETE FROM dr_replay_nonces target
         WHERE target.ctid IN (
            SELECT candidate.ctid
              FROM dr_replay_nonces candidate
             WHERE candidate.expires_at < cutoff
             ORDER BY candidate.expires_at, candidate.key_id, candidate.nonce
             FOR UPDATE SKIP LOCKED
             LIMIT row_limit
         )
         RETURNING target.key_id::text, target.nonce::text;
END;
$$;
"""


RECEIVER_SOURCE_XID_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_reject_receiver_source_xid() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
    IF current_setting('trading_bot.mutation_capability', true) = 'projection'
       AND current_setting('trading_bot.projection_scope', true) = 'receiver'
       AND EXISTS (
            SELECT 1 FROM dr_projection_service_roles service_role
             WHERE service_role.physical_site = cfg.physical_site
               AND service_role.service_scope = 'receiver'
               AND service_role.database_role = session_user
       )
       AND NEW.source_xid IS NOT NULL THEN
        RAISE EXCEPTION 'DR receiver cannot supply source-local transaction identity';
    END IF;
    RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    op.create_table(
        "webapp_writer_activation_operations",
        sa.Column("operation_id", sa.String(length=36), primary_key=True),
        sa.Column("status_request_id", sa.String(length=36), nullable=False),
        sa.Column("acquire_request_id", sa.String(length=36), nullable=False, unique=True),
        sa.Column("target_site", sa.String(length=16), nullable=False),
        sa.Column("target_epoch", sa.BigInteger(), nullable=False),
        sa.Column("predecessor_epoch", sa.BigInteger(), nullable=False),
        sa.Column("predecessor_lease_id", sa.String(length=64), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("proof_json", sa.Text(), nullable=True),
        sa.Column("proof_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "target_site IN ('webapp_fi','webapp_ir')",
            name="ck_webapp_writer_activation_site",
        ),
        sa.CheckConstraint(
            "target_epoch >= 2 AND predecessor_epoch = target_epoch - 1",
            name="ck_webapp_writer_activation_epoch",
        ),
        sa.CheckConstraint(
            "state IN ('planned','witness_acquired','local_activated')",
            name="ck_webapp_writer_activation_state",
        ),
        sa.CheckConstraint(
            "(state='planned' AND proof_json IS NULL AND proof_hash IS NULL) OR "
            "(state<>'planned' AND proof_json IS NOT NULL AND proof_hash IS NOT NULL)",
            name="ck_webapp_writer_activation_proof",
        ),
    )
    op.execute(CANONICAL_JSON_SQL)
    op.execute(EVENT_INTEGRITY_SQL)
    op.execute(NONCE_CLEANUP_SQL)
    op.execute(RECEIVER_SOURCE_XID_SQL)
    op.execute("DROP TRIGGER IF EXISTS trg_dr_receiver_source_xid ON dr_events")
    op.execute(
        "CREATE TRIGGER trg_dr_receiver_source_xid BEFORE INSERT ON dr_events "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_reject_receiver_source_xid()"
    )
    op.execute(
        "CREATE OR REPLACE FUNCTION trading_bot_require_same_transaction_dr_event() RETURNS trigger "
        "LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$ "
        "DECLARE cfg dr_database_runtime%ROWTYPE; state_row webapp_writer_state%ROWTYPE; "
        "row_json jsonb; pk_columns text[]; column_name text; identity_values jsonb := '[]'::jsonb; "
        "identity_value jsonb; aggregate_db_identity text; required_capability text; "
        "required_authority text; required_writer_epoch bigint; BEGIN "
        "SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id=1; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'three-site database runtime singleton is missing'; END IF; "
        "IF cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN RETURN NULL; END IF; "
        "IF cfg.physical_site='bot_fi' THEN required_capability := 'foreign_writer'; required_authority := 'foreign'; required_writer_epoch := NULL; "
        "ELSIF cfg.physical_site IN ('webapp_fi','webapp_ir') THEN required_capability := 'writer'; required_authority := 'webapp'; "
        "SELECT * INTO state_row FROM webapp_writer_state WHERE authority='webapp' FOR SHARE; "
        "IF NOT FOUND THEN RAISE EXCEPTION 'three-site event coverage writer state is missing'; END IF; required_writer_epoch := state_row.writer_epoch; "
        "ELSE RAISE EXCEPTION 'three-site event coverage physical site is invalid'; END IF; "
        "IF current_setting('trading_bot.mutation_capability', true) IS DISTINCT FROM required_capability "
        "THEN RAISE EXCEPTION 'authoritative event coverage requires % capability', required_capability; END IF; "
        "IF current_setting('trading_bot.physical_site', true) IS DISTINCT FROM cfg.physical_site "
        "THEN RAISE EXCEPTION 'authoritative event coverage physical site is missing or stale'; END IF; "
        "IF TG_OP='DELETE' THEN row_json := to_jsonb(OLD); ELSE row_json := to_jsonb(NEW); END IF; "
        "SELECT array_agg(attribute.attname ORDER BY key_column.ordinality) INTO pk_columns "
        "FROM pg_index index_definition CROSS JOIN LATERAL unnest(index_definition.indkey) WITH ORDINALITY AS key_column(attribute_number, ordinality) "
        "JOIN pg_attribute attribute ON attribute.attrelid=index_definition.indrelid AND attribute.attnum=key_column.attribute_number "
        "WHERE index_definition.indrelid=TG_RELID AND index_definition.indisprimary; "
        "IF pk_columns IS NULL THEN RAISE EXCEPTION 'authoritative table % lacks a primary key', TG_TABLE_NAME; END IF; "
        "FOREACH column_name IN ARRAY pk_columns LOOP identity_value := row_json -> column_name; "
        "IF identity_value IS NULL OR identity_value='null'::jsonb THEN RAISE EXCEPTION 'authoritative table % has an incomplete primary key', TG_TABLE_NAME; END IF; "
        "identity_values := identity_values || jsonb_build_array(identity_value); END LOOP; "
        "IF jsonb_array_length(identity_values)=1 THEN aggregate_db_identity := identity_values ->> 0; ELSE aggregate_db_identity := replace(identity_values::text, ', ', ','); END IF; "
        "IF NOT EXISTS (SELECT 1 FROM dr_events event WHERE event.source_xid=txid_current() "
        "AND event.aggregate_type=TG_TABLE_NAME AND event.aggregate_db_id=aggregate_db_identity "
        "AND event.operation=TG_OP AND event.origin_authority=required_authority "
        "AND event.origin_physical_site=cfg.physical_site "
        "AND event.writer_epoch IS NOT DISTINCT FROM required_writer_epoch "
        "AND event.tombstone IS NOT DISTINCT FROM (TG_OP='DELETE') "
        "AND trading_bot_projection_payload_matches(TG_TABLE_NAME, row_json, event.canonical_payload::jsonb) "
        "AND trading_bot_dr_event_integrity_valid(event.event_id)) THEN "
        "RAISE EXCEPTION 'authoritative mutation on %/% lacks a database-derived same-transaction DR event', TG_TABLE_NAME, aggregate_db_identity; END IF; "
        "RETURN NULL; END; $$"
    )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_canonical_jsonb(jsonb) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION trading_bot_sha256_jsonb(jsonb) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_integrity_valid(text) FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION trading_bot_cleanup_expired_replay_nonces(timestamptz, integer) FROM PUBLIC"
    )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_reject_receiver_source_xid() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION trading_bot_require_same_transaction_dr_event() FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "d542e3f4a6b7 is a forward-only security migration; use the reviewed restore/forward-rollback runbook"
    )
