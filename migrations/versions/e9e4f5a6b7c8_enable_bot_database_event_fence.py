"""Enable database event coverage for the Bot-FI authority.

Revision ID: e9e4f5a6b7c8
Revises: e8d3e4f5a6b7
"""

from alembic import op


revision = "e9e4f5a6b7c8"
down_revision = "e8d3e4f5a6b7"
branch_labels = None
depends_on = None


def _writer_trigger(*, bot_enabled: bool) -> str:
    bot_branch = """
            IF cfg.physical_site = 'bot_fi' THEN
                IF capability <> 'foreign_writer' OR session_user <> cfg.application_role THEN
                    RAISE EXCEPTION 'three-site foreign writer capability missing for role %', session_user;
                END IF;
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
    """ if bot_enabled else ""
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
                IF cfg.control_role IS NULL OR session_user <> cfg.control_role OR TG_TABLE_NAME <> 'dr_durability_state' THEN
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
            {bot_branch}
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
                OR state_row.witness_local_boot_id IS NULL
                OR state_row.witness_local_boottime_deadline IS NULL
                OR state_row.witness_local_boot_id <> trading_bot_boot_id()
                OR state_row.witness_local_boottime_deadline <= trading_bot_boottime_seconds()
            ) THEN
                RAISE EXCEPTION 'three-site writer witness lease is stale';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$
    """


def _event_coverage(*, bot_enabled: bool) -> str:
    capability = (
        "CASE WHEN cfg.physical_site = 'bot_fi' THEN 'foreign_writer' ELSE 'writer' END"
        if bot_enabled
        else "'writer'"
    )
    return f"""
        CREATE OR REPLACE FUNCTION trading_bot_require_same_transaction_dr_event() RETURNS trigger
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
        DECLARE
            cfg dr_database_runtime%ROWTYPE;
            row_json jsonb;
            pk_columns text[];
            column_name text;
            identity_values jsonb := '[]'::jsonb;
            identity_value jsonb;
            aggregate_db_identity text;
            required_capability text;
        BEGIN
            SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
            IF NOT cfg.enforcement_enabled OR session_user <> cfg.application_role THEN
                RETURN NULL;
            END IF;
            required_capability := {capability};
            IF current_setting('trading_bot.mutation_capability', true) <> required_capability THEN
                RAISE EXCEPTION 'authoritative event coverage requires % capability', required_capability;
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


def upgrade() -> None:
    op.drop_constraint("ck_dr_database_runtime_site", "dr_database_runtime", type_="check")
    op.create_check_constraint(
        "ck_dr_database_runtime_site",
        "dr_database_runtime",
        "physical_site IS NULL OR physical_site IN ('bot_fi', 'webapp_fi', 'webapp_ir')",
    )
    op.execute(_writer_trigger(bot_enabled=True))
    op.execute(_event_coverage(bot_enabled=True))
    op.execute("REVOKE ALL ON FUNCTION trading_bot_enforce_writer_term() FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION trading_bot_require_same_transaction_dr_event() FROM PUBLIC"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE dr_database_runtime SET enforcement_enabled=false, physical_site=NULL, "
        "application_role=NULL, projection_role=NULL, control_role=NULL "
        "WHERE physical_site='bot_fi'"
    )
    op.execute(_writer_trigger(bot_enabled=False))
    op.execute(_event_coverage(bot_enabled=False))
    op.execute("REVOKE ALL ON FUNCTION trading_bot_enforce_writer_term() FROM PUBLIC")
    op.execute(
        "REVOKE ALL ON FUNCTION trading_bot_require_same_transaction_dr_event() FROM PUBLIC"
    )
    op.drop_constraint("ck_dr_database_runtime_site", "dr_database_runtime", type_="check")
    op.create_check_constraint(
        "ck_dr_database_runtime_site",
        "dr_database_runtime",
        "physical_site IS NULL OR physical_site IN ('webapp_fi', 'webapp_ir')",
    )
