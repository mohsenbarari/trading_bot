"""Use database-side CLOCK_BOOTTIME for Writer lease enforcement.

Revision ID: e4f9a0b1c2d3
Revises: d3e8f9a0b1c2
"""

from alembic import op


revision = "e4f9a0b1c2d3"
down_revision = "d3e8f9a0b1c2"
branch_labels = None
depends_on = None


def _writer_trigger(*, monotonic: bool) -> str:
    lease_clause = (
        "state_row.witness_local_boot_id IS NULL "
        "OR state_row.witness_local_boottime_deadline IS NULL "
        "OR state_row.witness_local_boot_id <> trading_bot_boot_id() "
        "OR state_row.witness_local_boottime_deadline <= trading_bot_boottime_seconds()"
        if monotonic
        else "state_row.witness_lease_expires_at <= clock_timestamp() + interval '15 seconds'"
    )
    return f"""
        CREATE OR REPLACE FUNCTION trading_bot_enforce_writer_term() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
            cfg dr_database_runtime%ROWTYPE;
            state_row webapp_writer_state%ROWTYPE;
            capability text;
        BEGIN
            SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
            IF NOT cfg.enforcement_enabled THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            capability := current_setting('trading_bot.mutation_capability', true);
            IF capability = 'control' THEN
                IF session_user <> cfg.control_role OR TG_TABLE_NAME <> 'dr_durability_state' THEN
                    RAISE EXCEPTION 'three-site control capability rejected for role/table %/%', session_user, TG_TABLE_NAME;
                END IF;
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            IF capability = 'projection' THEN
                IF session_user <> cfg.projection_role OR NOT EXISTS (
                    SELECT 1 FROM dr_projection_table_allowlist WHERE table_name = TG_TABLE_NAME
                ) THEN
                    RAISE EXCEPTION 'three-site projection capability rejected for role/table %/%', session_user, TG_TABLE_NAME;
                END IF;
                IF TG_OP = 'UPDATE' AND EXISTS (
                    SELECT 1 FROM jsonb_each(to_jsonb(NEW)) AS candidate(column_name, new_value)
                    WHERE candidate.new_value IS DISTINCT FROM (to_jsonb(OLD) -> candidate.column_name)
                      AND NOT EXISTS (
                          SELECT 1 FROM dr_projection_field_allowlist allowed
                          WHERE allowed.table_name = TG_TABLE_NAME
                            AND allowed.column_name = candidate.column_name
                      )
                ) THEN
                    RAISE EXCEPTION 'three-site projection attempted a forbidden field on %', TG_TABLE_NAME;
                END IF;
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            IF capability <> 'writer' OR session_user <> cfg.application_role THEN
                RAISE EXCEPTION 'three-site writer capability missing for role %', session_user;
            END IF;
            SELECT * INTO state_row FROM webapp_writer_state WHERE authority = 'webapp' FOR SHARE;
            IF state_row.control_state <> 'active'
               OR state_row.active_site <> cfg.physical_site
               OR current_setting('trading_bot.physical_site', true) <> cfg.physical_site
               OR current_setting('trading_bot.writer_epoch', true) <> state_row.writer_epoch::text
               OR current_setting('trading_bot.transition_id', true) <> state_row.transition_id THEN
                RAISE EXCEPTION 'three-site writer term is stale';
            END IF;
            IF cfg.require_witness_lease AND (
                state_row.witness_lease_id IS NULL
                OR current_setting('trading_bot.witness_lease_id', true) <> state_row.witness_lease_id
                OR {lease_clause}
            ) THEN
                RAISE EXCEPTION 'three-site writer witness lease is stale';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$
    """


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS trading_bot_boottime")
    op.execute(_writer_trigger(monotonic=True))
    op.execute("REVOKE ALL ON FUNCTION trading_bot_enforce_writer_term() FROM PUBLIC")


def downgrade() -> None:
    op.execute(_writer_trigger(monotonic=False))
    op.execute("REVOKE ALL ON FUNCTION trading_bot_enforce_writer_term() FROM PUBLIC")
    op.execute("DROP EXTENSION IF EXISTS trading_bot_boottime")
