"""Add atomic transaction envelopes to the DR event protocol.

Revision ID: e6b1c2d3e4f5
Revises: e5a0b1c2d3e4
"""

import sqlalchemy as sa
from alembic import op


revision = "e6b1c2d3e4f5"
down_revision = "e5a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dr_destination_cursors",
        sa.Column("origin_authority", sa.String(16), primary_key=True),
        sa.Column("origin_physical_site", sa.String(16), primary_key=True),
        sa.Column("producer_epoch", sa.BigInteger(), primary_key=True),
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("producer_epoch >= 1", name="ck_dr_destination_cursors_epoch"),
        sa.CheckConstraint("last_sequence >= 0", name="ck_dr_destination_cursors_sequence"),
        sa.CheckConstraint(
            "destination_site IN ('bot_fi','webapp_fi','webapp_ir')",
            name="ck_dr_destination_cursors_site",
        ),
    )
    op.add_column("dr_events", sa.Column("transaction_id", sa.String(36)))
    op.add_column("dr_events", sa.Column("transaction_position", sa.Integer()))
    op.add_column("dr_events", sa.Column("transaction_size", sa.Integer()))
    op.add_column("dr_events", sa.Column("transaction_hash", sa.String(64)))
    op.add_column("dr_events", sa.Column("destination_streams", sa.JSON()))
    op.create_unique_constraint(
        "ux_dr_events_transaction_position",
        "dr_events",
        ["origin_physical_site", "producer_epoch", "transaction_id", "transaction_position"],
    )
    op.create_unique_constraint(
        "ux_dr_event_receipts_destination_stream",
        "dr_event_receipts",
        ["destination_site", "origin_physical_site", "producer_epoch", "producer_sequence"],
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
    op.execute(
        """
        CREATE FUNCTION trading_bot_dr_event_finalized() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            event_row dr_events%ROWTYPE;
            database_runtime dr_database_runtime%ROWTYPE;
            destination record;
            group_count bigint;
            position_count bigint;
            minimum_position integer;
            maximum_position integer;
            group_hash_count bigint;
        BEGIN
            -- A deferred INSERT trigger retains the INSERT-time NEW image.
            -- Re-read the immutable event so finalization performed later in
            -- the same transaction is what the commit gate validates.
            SELECT * INTO event_row FROM dr_events WHERE event_id = NEW.event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'protocol-v2 DR event disappeared before commit';
            END IF;
            SELECT * INTO database_runtime FROM dr_database_runtime WHERE singleton_id = 1;
            IF NOT database_runtime.enforcement_enabled
               OR session_user <> database_runtime.application_role THEN
                RETURN NULL;
            END IF;
            IF event_row.protocol_version = 2 AND (
                event_row.transaction_id IS NULL
                OR event_row.transaction_position IS NULL
                OR event_row.transaction_size IS NULL
                OR event_row.transaction_size < 1
                OR event_row.transaction_position < 1
                OR event_row.transaction_position > event_row.transaction_size
                OR event_row.transaction_hash IS NULL
                OR event_row.transaction_hash = repeat('0', 64)
                OR event_row.envelope_hash = repeat('0', 64)
                OR event_row.destination_streams IS NULL
                OR event_row.destination_streams::jsonb = '{}'::jsonb
                OR (SELECT count(*) FROM dr_events member
                    WHERE member.origin_physical_site = event_row.origin_physical_site
                      AND member.producer_epoch = event_row.producer_epoch
                      AND member.transaction_id = event_row.transaction_id) <> event_row.transaction_size
            ) THEN
                RAISE EXCEPTION 'protocol-v2 DR transaction is incomplete at commit';
            END IF;
            IF event_row.protocol_version = 2 THEN
                FOR destination IN
                    SELECT key AS site, value AS stream
                    FROM jsonb_each(event_row.destination_streams::jsonb)
                LOOP
                    IF destination.site NOT IN ('bot_fi','webapp_fi','webapp_ir')
                       OR destination.site = event_row.origin_physical_site
                       OR (destination.stream ->> 'sequence')::bigint < 1
                       OR destination.stream ->> 'transaction_id' <> event_row.transaction_id
                       OR (destination.stream ->> 'transaction_position')::integer < 1
                       OR (destination.stream ->> 'transaction_size')::integer < 1
                       OR (destination.stream ->> 'transaction_position')::integer
                          > (destination.stream ->> 'transaction_size')::integer
                       OR destination.stream ->> 'transaction_hash' !~ '^[0-9a-f]{64}$'
                       OR destination.stream ->> 'transaction_hash' = repeat('0', 64) THEN
                        RAISE EXCEPTION 'protocol-v2 DR destination stream is malformed';
                    END IF;
                    SELECT count(*),
                           count(DISTINCT (member.destination_streams::jsonb -> destination.site ->> 'transaction_position')::integer),
                           min((member.destination_streams::jsonb -> destination.site ->> 'transaction_position')::integer),
                           max((member.destination_streams::jsonb -> destination.site ->> 'transaction_position')::integer),
                           count(DISTINCT member.destination_streams::jsonb -> destination.site ->> 'transaction_hash')
                      INTO group_count, position_count, minimum_position,
                           maximum_position, group_hash_count
                      FROM dr_events member
                     WHERE member.origin_physical_site = event_row.origin_physical_site
                       AND member.producer_epoch = event_row.producer_epoch
                       AND member.transaction_id = event_row.transaction_id
                       AND member.destination_streams::jsonb ? destination.site;
                    IF group_count <> (destination.stream ->> 'transaction_size')::integer
                       OR position_count <> group_count
                       OR minimum_position <> 1
                       OR maximum_position <> group_count
                       OR group_hash_count <> 1 THEN
                        RAISE EXCEPTION 'protocol-v2 DR destination transaction is incomplete';
                    END IF;
                END LOOP;
            END IF;
            RETURN NULL;
        END; $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_dr_event_finalized "
        "AFTER INSERT OR UPDATE ON dr_events DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_event_finalized()"
    )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_finalized() FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_dr_event_finalized ON dr_events")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_event_finalized()")
    op.execute("DROP TRIGGER IF EXISTS trg_dr_events_immutable ON dr_events")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_event_immutable()")
    op.execute(
        "CREATE FUNCTION trading_bot_dr_event_immutable() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'dr_events are immutable'; END; $$"
    )
    op.execute(
        "CREATE TRIGGER trg_dr_events_immutable BEFORE UPDATE OR DELETE ON dr_events "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_event_immutable()"
    )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_immutable() FROM PUBLIC")
    op.drop_constraint("ux_dr_events_transaction_position", "dr_events", type_="unique")
    op.drop_constraint(
        "ux_dr_event_receipts_destination_stream", "dr_event_receipts", type_="unique"
    )
    op.drop_column("dr_events", "destination_streams")
    op.drop_column("dr_events", "transaction_hash")
    op.drop_column("dr_events", "transaction_size")
    op.drop_column("dr_events", "transaction_position")
    op.drop_column("dr_events", "transaction_id")
    op.drop_table("dr_destination_cursors")
