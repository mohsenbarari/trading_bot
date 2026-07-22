"""Harden the DR history and upgrade-boundary contract.

Revision ID: b986c7d8e0f1
Revises: a875b6c7d9e0
"""

from __future__ import annotations

from alembic import op


revision = "b986c7d8e0f1"
down_revision = "a875b6c7d9e0"
branch_labels = None
depends_on = None


# SHARE ROW EXCLUSIVE excludes INSERT/UPDATE/DELETE (ROW EXCLUSIVE) writers
# while the final history snapshot is validated and the replacement guard is
# installed.  The lock is intentionally taken in one statement and before any
# validation so PostgreSQL cannot admit an old-rule commit in the gap.
WRITE_EXCLUDING_LOCK_SQL = r"""
LOCK TABLE
    dr_events,
    dr_event_destination_sequences,
    dr_producer_cursors,
    dr_destination_cursors
IN SHARE ROW EXCLUSIVE MODE;
"""


HISTORY_PREFLIGHT_SQL = r"""
DO $$
DECLARE
    bad_event_id text;
    bad_stream text;
BEGIN
    -- Strict v2 integrity and allocation binding apply to database-bound
    -- producer events.  A received event deliberately has source_xid=NULL.
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

    -- Producer history is deliberately cursor-led.  v1 and pre-source_xid
    -- members are part of the retained 1..tail producer stream; only the
    -- field-specific integrity checks above are v2/database-bound-only.
    SELECT format('%s/%s/%s producer', cursor.origin_authority,
                  cursor.origin_physical_site, cursor.producer_epoch)
      INTO bad_stream
      FROM dr_producer_cursors cursor
      LEFT JOIN dr_events event
        ON event.origin_authority=cursor.origin_authority
       AND event.origin_physical_site=cursor.origin_physical_site
       AND event.producer_epoch=cursor.producer_epoch
     GROUP BY cursor.origin_authority, cursor.origin_physical_site,
              cursor.producer_epoch, cursor.last_sequence
    HAVING cursor.last_sequence < 0
        OR (cursor.last_sequence=0 AND count(event.event_id) <> 0)
        OR (cursor.last_sequence>0 AND (
               count(event.event_id) <> cursor.last_sequence
            OR min(event.producer_sequence) <> 1
            OR max(event.producer_sequence) <> cursor.last_sequence
            OR count(DISTINCT event.producer_sequence) <> count(event.event_id)
        ))
     ORDER BY cursor.origin_authority, cursor.origin_physical_site,
              cursor.producer_epoch
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight found an incomplete % stream', bad_stream
          USING DETAIL = 'retained producer history must be exactly contiguous from 1 through the cursor tail';
    END IF;

    -- Every database-bound v2 producer stream must also have a cursor.  This
    -- does not reject receiver-only events, whose source_xid is intentionally
    -- NULL and which are tracked by receiver checkpoints instead.
    SELECT format('%s/%s/%s producer', event.origin_authority,
                  event.origin_physical_site, event.producer_epoch)
      INTO bad_stream
      FROM dr_events event
      LEFT JOIN dr_producer_cursors cursor
        ON cursor.origin_authority=event.origin_authority
       AND cursor.origin_physical_site=event.origin_physical_site
       AND cursor.producer_epoch=event.producer_epoch
     WHERE event.source_xid IS NOT NULL
       AND event.protocol_version=2
       AND cursor.origin_authority IS NULL
     ORDER BY event.origin_authority, event.origin_physical_site,
              event.producer_epoch
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight found a cursorless % stream', bad_stream;
    END IF;

    -- Destination allocation began with protocol v2.  Use the signed v2
    -- destination envelope as historical evidence so a legitimate
    -- pre-source_xid prefix is accepted, while modern database-bound rows are
    -- additionally certified by the binding check above.
    SELECT format('%s/%s/%s/%s destination', cursor.origin_authority,
                  cursor.origin_physical_site, cursor.producer_epoch,
                  cursor.destination_site)
      INTO bad_stream
      FROM dr_destination_cursors cursor
      LEFT JOIN LATERAL (
          SELECT count(*) AS member_count,
                 count(DISTINCT (event.destination_streams::jsonb
                     -> cursor.destination_site ->> 'sequence')::bigint) AS distinct_count,
                 min((event.destination_streams::jsonb
                     -> cursor.destination_site ->> 'sequence')::bigint) AS minimum_sequence,
                 max((event.destination_streams::jsonb
                     -> cursor.destination_site ->> 'sequence')::bigint) AS maximum_sequence
            FROM dr_events event
           WHERE event.origin_authority=cursor.origin_authority
             AND event.origin_physical_site=cursor.origin_physical_site
             AND event.producer_epoch=cursor.producer_epoch
             AND event.protocol_version=2
             AND jsonb_typeof(event.destination_streams::jsonb)='object'
             AND event.destination_streams::jsonb ? cursor.destination_site
             AND jsonb_typeof(event.destination_streams::jsonb
                     -> cursor.destination_site)='object'
             AND trading_bot_dr_json_positive_integer(
                     event.destination_streams::jsonb
                         -> cursor.destination_site -> 'sequence',
                     9223372036854775807
                 ) IS TRUE
      ) history ON true
    WHERE cursor.last_sequence < 0
       OR (cursor.last_sequence=0 AND history.member_count <> 0)
       OR (cursor.last_sequence>0 AND (
              history.member_count <> cursor.last_sequence
           OR history.distinct_count <> history.member_count
           OR history.minimum_sequence <> 1
           OR history.maximum_sequence <> cursor.last_sequence
       ))
     ORDER BY cursor.origin_authority, cursor.origin_physical_site,
              cursor.producer_epoch, cursor.destination_site
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight found an incomplete % stream', bad_stream
          USING DETAIL = 'retained destination history must be exactly contiguous from 1 through the cursor tail';
    END IF;

    -- A modern local binding without a corresponding destination cursor is
    -- corruption even if no cursor-led group exists from which to discover it.
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
     WHERE event.source_xid IS NOT NULL
       AND event.protocol_version=2
       AND cursor.origin_authority IS NULL
     ORDER BY binding.origin_authority, binding.origin_physical_site,
              binding.producer_epoch, binding.destination_site
     LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'DR history preflight found a cursorless % stream', bad_stream;
    END IF;
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
       AND NEW.source_xid IS NOT DISTINCT FROM OLD.source_xid
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


def upgrade() -> None:
    op.execute(WRITE_EXCLUDING_LOCK_SQL)
    op.execute(HISTORY_PREFLIGHT_SQL)
    op.execute(DR_EVENT_IMMUTABILITY_SQL)
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_immutable() FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "b986c7d8e0f1 is a forward-only DR safety migration; use the reviewed restore/forward-rollback runbook"
    )
