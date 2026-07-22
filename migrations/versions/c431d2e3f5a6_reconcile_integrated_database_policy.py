"""Reconcile queue/DR database policy after the integration merge.

Revision ID: c431d2e3f5a6
Revises: b320c1d2e3f4
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "c431d2e3f5a6"
down_revision = "b320c1d2e3f4"
branch_labels = None
depends_on = None


SYNC_PROJECTION_TABLES = (
    "accountant_relations", "admin_broadcast_messages", "admin_market_messages",
    "commodities", "commodity_aliases", "customer_relations", "invitations",
    "market_runtime_state", "market_schedule_overrides", "notifications",
    "offer_publication_states", "offer_requests", "offers", "trades",
    "trade_delivery_receipts", "telegram_link_tokens", "telegram_admin_broadcasts",
    "telegram_admin_broadcast_receipts", "telegram_notification_outbox",
    "trading_settings", "user_blocks", "user_notification_preferences", "users",
)
LOCAL_PROJECTION_TABLES = (
    "sync_apply_watermarks", "sync_blocks", "chats", "chat_members",
    "user_counter_event_receipts", "dr_events", "dr_event_receipts",
    "dr_event_deliveries", "dr_stream_checkpoints", "dr_conflict_quarantine",
    "dr_replay_nonces", "dr_effect_outbox", "dr_effect_fanouts",
    "dr_producer_cursors", "dr_projection_versions", "dr_blob_manifests",
    "dr_file_intents", "dr_blob_deliveries", "dr_blob_receipts",
    "dr_recovery_manifests",
)
WEBAPP_DR_PROJECTION_TABLES = (
    "chat_files", "chat_members", "chats", "conversations",
    "invitation_identity_reservations", "invitation_sms_deliveries", "messages",
    "push_subscriptions", "session_login_requests",
    "single_session_recovery_admin_targets", "single_session_recovery_requests",
    "user_sessions",
)
PROJECTION_TABLES = tuple(
    sorted(set(SYNC_PROJECTION_TABLES + LOCAL_PROJECTION_TABLES + WEBAPP_DR_PROJECTION_TABLES))
)

# This is the executable database counterpart of the integrated field policy.
# WebApp-private Push endpoint/key material is intentionally retained; only its
# local diagnostics/fingerprint fields are removed from the FI/IR stream.
PROJECTION_FORBIDDEN_FIELDS = frozenset(
    {
        ("chats", "avatar_file_id"),
        ("dr_events", "source_xid"),
        ("invitation_identity_reservations", "normalized_account_name"),
        ("invitation_identity_reservations", "normalized_mobile"),
        ("offer_publication_states", "error_code"),
        ("offer_publication_states", "error_message"),
        ("offer_publication_states", "last_attempt_at"),
        ("offer_publication_states", "last_success_at"),
        ("offer_publication_states", "next_retry_at"),
        ("offer_publication_states", "offer_id"),
        ("offer_publication_states", "state_metadata"),
        ("offer_publication_states", "surface_resource_id"),
        ("offer_publication_states", "telegram_chat_id"),
        ("offer_publication_states", "telegram_message_id"),
        ("push_subscriptions", "last_error"),
        ("push_subscriptions", "platform"),
        ("push_subscriptions", "user_agent"),
        ("telegram_admin_broadcast_receipts", "lease_until"),
        ("telegram_admin_broadcast_receipts", "queue_handed_off_at"),
        ("telegram_admin_broadcast_receipts", "queue_job_id"),
        ("telegram_admin_broadcast_receipts", "worker_id"),
        ("telegram_admin_broadcasts", "queue_last_handed_off_at"),
        ("telegram_notification_outbox", "lease_until"),
        ("telegram_notification_outbox", "queue_handed_off_at"),
        ("telegram_notification_outbox", "queue_job_id"),
        ("telegram_notification_outbox", "worker_id"),
        ("trade_delivery_receipts", "lease_until"),
        ("trade_delivery_receipts", "notification_id"),
        ("trade_delivery_receipts", "offer_id"),
        ("trade_delivery_receipts", "trade_id"),
        ("trade_delivery_receipts", "worker_id"),
        ("users", "admin_password_hash"),
        ("users", "avatar_file_id"),
        ("users", "must_change_password"),
        ("users", "normalized_account_name"),
        ("users", "normalized_mobile_number"),
    }
)

BOT_LOCAL_EXECUTION_TABLES = (
    "telegram_delivery_jobs",
    "telegram_delivery_provider_outcomes",
    "telegram_delivery_reconciliation_evidence",
    "telegram_delivery_runtime_gates",
    "telegram_delivery_resume_operations",
    "telegram_delivery_feeder_states",
    "telegram_scheduled_operations",
    "telegram_interaction_anchor_states",
    "telegram_channel_membership_sagas",
)
ADDED_FENCED_TABLES = BOT_LOCAL_EXECUTION_TABLES + ("dr_destination_cursors",)
PROJECTION_SERVICE_SCOPES = ("receiver", "delivery", "projector", "blob", "effect")


WRITER_FUNCTION_SQL = r"""
CREATE OR REPLACE FUNCTION trading_bot_enforce_writer_term() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
    cfg dr_database_runtime%ROWTYPE;
    state_row webapp_writer_state%ROWTYPE;
    capability text;
    projection_scope text;
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'three-site database runtime singleton is missing';
    END IF;
    IF cfg.enforcement_enabled IS NOT TRUE THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF cfg.physical_site IS NULL OR cfg.application_role IS NULL OR cfg.projection_role IS NULL THEN
        RAISE EXCEPTION 'three-site database runtime identity is incomplete';
    END IF;
    capability := current_setting('trading_bot.mutation_capability', true);
    IF capability = 'control' THEN
        IF cfg.control_role IS NULL
           OR session_user IS DISTINCT FROM cfg.control_role
           OR TG_TABLE_NAME <> 'dr_durability_state' THEN
            RAISE EXCEPTION 'three-site control capability rejected for role/table %/%', session_user, TG_TABLE_NAME;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF capability = 'projection' THEN
        projection_scope := current_setting('trading_bot.projection_scope', true);
        IF projection_scope IS NULL OR NOT EXISTS (
            SELECT 1 FROM dr_projection_service_roles service_role
             WHERE service_role.physical_site = cfg.physical_site
               AND service_role.service_scope = projection_scope
               AND service_role.database_role = session_user
        ) OR NOT EXISTS (
            SELECT 1 FROM dr_projection_table_allowlist WHERE table_name = TG_TABLE_NAME
        ) THEN
            RAISE EXCEPTION 'three-site projection capability rejected for role/scope/table %/%/%',
                session_user, projection_scope, TG_TABLE_NAME;
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
    IF cfg.physical_site = 'bot_fi' THEN
        IF capability IS DISTINCT FROM 'foreign_writer'
           OR session_user IS DISTINCT FROM cfg.application_role
           OR current_setting('trading_bot.physical_site', true) IS DISTINCT FROM 'bot_fi' THEN
            RAISE EXCEPTION 'three-site foreign writer capability missing for role %', session_user;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF cfg.physical_site NOT IN ('webapp_fi', 'webapp_ir') OR cfg.control_role IS NULL THEN
        RAISE EXCEPTION 'three-site WebApp runtime identity is incomplete';
    END IF;
    IF capability IS DISTINCT FROM 'writer' OR session_user IS DISTINCT FROM cfg.application_role THEN
        RAISE EXCEPTION 'three-site writer capability missing for role %', session_user;
    END IF;
    SELECT * INTO state_row FROM webapp_writer_state WHERE authority = 'webapp' FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'three-site WebApp writer state is missing';
    END IF;
    IF state_row.control_state IS DISTINCT FROM 'active'
       OR state_row.active_site IS DISTINCT FROM cfg.physical_site
       OR current_setting('trading_bot.physical_site', true) IS DISTINCT FROM cfg.physical_site
       OR current_setting('trading_bot.writer_epoch', true) IS DISTINCT FROM state_row.writer_epoch::text
       OR current_setting('trading_bot.transition_id', true) IS DISTINCT FROM state_row.transition_id THEN
        RAISE EXCEPTION 'three-site writer term is stale';
    END IF;
    IF cfg.require_witness_lease IS TRUE AND (
        state_row.witness_lease_id IS NULL
        OR current_setting('trading_bot.witness_lease_id', true) IS DISTINCT FROM state_row.witness_lease_id
        OR state_row.witness_local_boot_id IS NULL
        OR state_row.witness_local_boottime_deadline IS NULL
        OR state_row.witness_local_boot_id IS DISTINCT FROM trading_bot_boot_id()
        OR state_row.witness_local_boottime_deadline <= trading_bot_boottime_seconds()
    ) THEN
        RAISE EXCEPTION 'three-site writer witness lease is stale';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$
"""


PAYLOAD_MATCH_FUNCTION_SQL = r"""
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
        IF source_value IS NOT DISTINCT FROM supplied_value THEN
            CONTINUE;
        END IF;
        -- SQLAlchemy normalizes Decimal values as JSON strings while
        -- PostgreSQL to_jsonb retains a JSON number. Preserve that intentional
        -- transport representation without accepting other type changes.
        IF jsonb_typeof(source_value) = 'number'
           AND jsonb_typeof(supplied_value) = 'string'
           AND source_value #>> '{}' = supplied_value #>> '{}' THEN
            CONTINUE;
        END IF;
        RETURN false;
    END LOOP;
    RETURN true;
END;
$$
"""


EVENT_COVERAGE_FUNCTION_SQL = r"""
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
BEGIN
    SELECT * INTO cfg FROM dr_database_runtime WHERE singleton_id = 1;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'three-site database runtime singleton is missing';
    END IF;
    IF cfg.enforcement_enabled IS NOT TRUE OR session_user IS DISTINCT FROM cfg.application_role THEN
        RETURN NULL;
    END IF;
    IF cfg.physical_site IS NULL OR cfg.application_role IS NULL THEN
        RAISE EXCEPTION 'three-site event coverage runtime identity is incomplete';
    END IF;
    IF cfg.physical_site = 'bot_fi' THEN
        required_capability := 'foreign_writer';
        required_authority := 'foreign';
        required_writer_epoch := NULL;
    ELSIF cfg.physical_site IN ('webapp_fi', 'webapp_ir') THEN
        required_capability := 'writer';
        required_authority := 'webapp';
        SELECT * INTO state_row FROM webapp_writer_state WHERE authority = 'webapp' FOR SHARE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'three-site event coverage writer state is missing';
        END IF;
        required_writer_epoch := state_row.writer_epoch;
    ELSE
        RAISE EXCEPTION 'three-site event coverage physical site is invalid';
    END IF;
    IF current_setting('trading_bot.mutation_capability', true) IS DISTINCT FROM required_capability THEN
        RAISE EXCEPTION 'authoritative event coverage requires % capability', required_capability;
    END IF;
    IF current_setting('trading_bot.physical_site', true) IS DISTINCT FROM cfg.physical_site THEN
        RAISE EXCEPTION 'authoritative event coverage physical site is missing or stale';
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
           AND event.origin_authority = required_authority
           AND event.origin_physical_site = cfg.physical_site
           AND event.writer_epoch IS NOT DISTINCT FROM required_writer_epoch
           AND event.tombstone IS NOT DISTINCT FROM (TG_OP = 'DELETE')
           AND event.canonical_payload_hash ~ '^[0-9a-f]{64}$'
           AND event.envelope_hash ~ '^[0-9a-f]{64}$'
           AND event.envelope_hash <> repeat('0', 64)
           AND trading_bot_projection_payload_matches(
               TG_TABLE_NAME, row_json, event.canonical_payload::jsonb
           )
    ) THEN
        RAISE EXCEPTION 'authoritative mutation on %/% lacks a semantically bound same-transaction DR event',
            TG_TABLE_NAME, aggregate_db_identity;
    END IF;
    RETURN NULL;
END;
$$
"""


def _reconcile_projection_policy() -> None:
    connection = op.get_bind()
    op.execute("DELETE FROM dr_projection_field_allowlist")
    op.execute("DELETE FROM dr_projection_table_allowlist")
    for table_name in PROJECTION_TABLES:
        op.execute(
            sa.text(
                "INSERT INTO dr_projection_table_allowlist (table_name) VALUES (:table_name)"
            ).bindparams(table_name=table_name)
        )
        columns = connection.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:table_name "
                "ORDER BY ordinal_position"
            ),
            {"table_name": table_name},
        ).scalars().all()
        if not columns:
            raise RuntimeError(f"integrated projection table is missing: {table_name}")
        for column_name in columns:
            if (table_name, str(column_name)) in PROJECTION_FORBIDDEN_FIELDS:
                continue
            op.execute(
                sa.text(
                    "INSERT INTO dr_projection_field_allowlist (table_name, column_name) "
                    "VALUES (:table_name, :column_name)"
                ).bindparams(table_name=table_name, column_name=str(column_name))
            )


def upgrade() -> None:
    # Mark the source-local field as server-generated NULL for projection ORM
    # inserts. Local producers continue to set txid_current() explicitly.
    op.alter_column("dr_events", "source_xid", server_default=sa.text("NULL"))
    op.create_index(
        "ix_dr_replay_nonces_expires_at",
        "dr_replay_nonces",
        ["expires_at"],
        unique=False,
    )
    op.create_table(
        "dr_projection_service_roles",
        sa.Column("physical_site", sa.String(length=32), nullable=False),
        sa.Column("service_scope", sa.String(length=32), nullable=False),
        sa.Column("database_role", sa.String(length=63), nullable=False),
        sa.PrimaryKeyConstraint("physical_site", "service_scope"),
        sa.UniqueConstraint("database_role"),
        sa.CheckConstraint(
            "physical_site IN ('bot_fi','webapp_fi','webapp_ir')",
            name="ck_dr_projection_service_role_site",
        ),
        sa.CheckConstraint(
            "service_scope IN ('receiver','delivery','projector','blob','effect')",
            name="ck_dr_projection_service_role_scope",
        ),
        sa.CheckConstraint(
            "database_role ~ '^[a-z_][a-z0-9_]{0,62}$'",
            name="ck_dr_projection_service_role_name",
        ),
    )
    _reconcile_projection_policy()
    op.execute(PAYLOAD_MATCH_FUNCTION_SQL)
    op.execute(WRITER_FUNCTION_SQL)
    op.execute(EVENT_COVERAGE_FUNCTION_SQL)
    # The deferred validator must inspect the complete immutable event row,
    # including source_xid, without granting that local-only column to any DR
    # runtime role. Keep the trigger function non-callable and run only its
    # internal validation query with the migration owner's authority.
    op.execute(
        "ALTER FUNCTION trading_bot_dr_event_finalized() SECURITY DEFINER"
    )
    op.execute(
        "ALTER FUNCTION trading_bot_dr_event_finalized() SET search_path = public, pg_temp"
    )
    for table_name in ADDED_FENCED_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS trg_three_site_writer_term ON {table_name}")
        op.execute(
            f"CREATE TRIGGER trg_three_site_writer_term BEFORE INSERT OR UPDATE OR DELETE "
            f"ON {table_name} FOR EACH ROW EXECUTE FUNCTION trading_bot_enforce_writer_term()"
        )
    op.execute("REVOKE ALL ON FUNCTION trading_bot_projection_payload_matches(text, jsonb, jsonb) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION trading_bot_dr_event_finalized() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION trading_bot_enforce_writer_term() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION trading_bot_require_same_transaction_dr_event() FROM PUBLIC")


def downgrade() -> None:
    raise RuntimeError(
        "c431d2e3f5a6 is a forward-only security reconciliation; use the reviewed restore/forward-rollback runbook"
    )
