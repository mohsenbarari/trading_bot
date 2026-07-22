"""add three-site DR event, effect, replay, and database fence plane

Revision ID: d3e8f9a0b1c2
Revises: d2e7f8a9b0c1
Create Date: 2026-07-19 18:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d3e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "d2e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    "chats", "chat_members", "sync_apply_watermarks", "sync_blocks",
    "user_counter_event_receipts", "dr_producer_cursors", "dr_events",
    "dr_event_deliveries", "dr_event_receipts", "dr_stream_checkpoints",
    "dr_projection_versions", "dr_conflict_quarantine", "dr_replay_nonces",
    "dr_effect_outbox", "dr_blob_manifests", "dr_file_intents",
    "dr_blob_deliveries", "dr_blob_receipts", "dr_recovery_manifests",
)
WEBAPP_DR_PROJECTION_TABLES = (
    "chat_files", "conversations", "invitation_identity_reservations",
    "invitation_sms_deliveries", "messages", "session_login_requests",
    "single_session_recovery_admin_targets", "single_session_recovery_requests",
    "user_sessions",
)
PROJECTION_FORBIDDEN_FIELDS = (
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
    ("telegram_admin_broadcast_receipts", "lease_until"),
    ("telegram_admin_broadcast_receipts", "worker_id"),
    ("telegram_notification_outbox", "lease_until"),
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
)
WRITER_FENCED_TABLES = tuple(sorted(set(SYNC_PROJECTION_TABLES + LOCAL_PROJECTION_TABLES + (
    "chat_files", "conversations", "invitation_identity_reservations",
    "invitation_sms_deliveries", "dr_durability_state", "market_channel_notice_receipts", "messages",
    "push_subscriptions", "session_login_requests", "single_session_recovery_admin_targets",
    "single_session_recovery_requests", "telegram_registration_command_receipts",
    "telegram_registration_intents", "upload_batches", "upload_sessions", "user_sessions",
))))


def _writer_clock_columns() -> None:
    op.add_column("webapp_writer_state", sa.Column("witness_lease_issued_at", sa.DateTime(timezone=True)))
    op.add_column("webapp_writer_state", sa.Column("witness_local_boot_id", sa.String(36)))
    op.add_column("webapp_writer_state", sa.Column("witness_local_boottime_deadline", sa.Float()))
    op.add_column("webapp_writer_state", sa.Column("witness_observed_wall_at", sa.DateTime(timezone=True)))
    op.add_column("webapp_writer_state", sa.Column("witness_observed_boottime", sa.Float()))
    op.add_column("webapp_writer_state", sa.Column("witness_clock_offset_ms", sa.BigInteger()))


def _event_tables() -> None:
    op.create_table(
        "dr_producer_cursors",
        sa.Column("origin_authority", sa.String(16), primary_key=True),
        sa.Column("origin_physical_site", sa.String(16), primary_key=True),
        sa.Column("producer_epoch", sa.BigInteger(), primary_key=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "dr_events",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("origin_authority", sa.String(16), nullable=False),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("producer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("producer_sequence", sa.BigInteger(), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(255), nullable=False),
        sa.Column("aggregate_db_id", sa.String(64)),
        sa.Column("aggregate_version", sa.BigInteger()),
        sa.Column("operation", sa.String(10), nullable=False),
        sa.Column("canonical_payload", sa.JSON(), nullable=False),
        sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("causation_id", sa.String(128)),
        sa.Column("idempotency_key", sa.String(255)),
        sa.Column("writer_epoch", sa.BigInteger()),
        sa.Column("tombstone", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("producer_epoch >= 1", name="ck_dr_events_epoch_positive"),
        sa.CheckConstraint("producer_sequence >= 1", name="ck_dr_events_sequence_positive"),
        sa.CheckConstraint("operation IN ('INSERT', 'UPDATE', 'DELETE')", name="ck_dr_events_operation"),
        sa.UniqueConstraint("origin_physical_site", "producer_epoch", "producer_sequence", name="ux_dr_events_stream_sequence"),
    )
    op.create_index("ix_dr_events_aggregate", "dr_events", ["aggregate_type", "aggregate_id", "aggregate_version"])
    op.create_index("ix_dr_events_stream", "dr_events", ["origin_physical_site", "producer_epoch", "producer_sequence"])
    op.create_table(
        "dr_event_deliveries",
        sa.Column("event_id", sa.String(36), sa.ForeignKey("dr_events.event_id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledgement_hash", sa.String(64)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("relay_site", sa.String(16)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending', 'inflight', 'acknowledged', 'blocked_gap', 'quarantined')", name="ck_dr_event_deliveries_status"),
    )
    op.create_index("ix_dr_event_deliveries_ready", "dr_event_deliveries", ["destination_site", "status", "next_attempt_at"])
    op.create_table(
        "dr_event_receipts",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("producer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("producer_sequence", sa.BigInteger(), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("received_from_site", sa.String(16), nullable=False),
        sa.Column("relay_site", sa.String(16)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('received', 'applied', 'duplicate', 'blocked_gap', 'quarantined')", name="ck_dr_event_receipts_status"),
    )
    op.create_table(
        "dr_stream_checkpoints",
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("origin_physical_site", sa.String(16), primary_key=True),
        sa.Column("producer_epoch", sa.BigInteger(), primary_key=True),
        sa.Column("contiguous_received_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("contiguous_applied_sequence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_envelope_hash", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "dr_projection_versions",
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("origin_authority", sa.String(16), primary_key=True),
        sa.Column("aggregate_type", sa.String(64), primary_key=True),
        sa.Column("aggregate_id", sa.String(255), primary_key=True),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("producer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("producer_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("envelope_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("producer_epoch >= 1", name="ck_dr_projection_versions_epoch"),
        sa.CheckConstraint("producer_sequence >= 1", name="ck_dr_projection_versions_sequence"),
        sa.CheckConstraint("origin_authority IN ('foreign', 'webapp')", name="ck_dr_projection_versions_authority"),
    )
    op.create_table(
        "dr_conflict_quarantine",
        sa.Column("quarantine_id", sa.String(36), primary_key=True),
        sa.Column("destination_site", sa.String(16), nullable=False),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("producer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("producer_sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("expected_hash", sa.String(64)),
        sa.Column("received_hash", sa.String(64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by", sa.String(128)),
        sa.Column("resolution", sa.Text()),
    )
    op.create_index("ix_dr_conflict_unresolved", "dr_conflict_quarantine", ["resolved_at", "created_at"])
    op.create_table(
        "dr_replay_nonces",
        sa.Column("key_id", sa.String(64), primary_key=True),
        sa.Column("nonce", sa.String(64), primary_key=True),
        sa.Column("source_site", sa.String(16), nullable=False),
        sa.Column("destination_site", sa.String(16), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "dr_effect_outbox",
        sa.Column("effect_id", sa.String(36), primary_key=True),
        sa.Column("event_id", sa.String(36), sa.ForeignKey("dr_events.event_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("executor_site", sa.String(16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger()),
        sa.Column("effect_type", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("destination_key_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.String(128)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("provider_receipt_hash", sa.String(64)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("idempotency_key", name="ux_dr_effect_outbox_idempotency"),
        sa.CheckConstraint("status IN ('pending', 'inflight', 'succeeded', 'failed', 'ambiguous', 'cancelled_stale_epoch')", name="ck_dr_effect_outbox_status"),
    )
    op.create_index("ix_dr_effect_outbox_ready", "dr_effect_outbox", ["executor_site", "status", "next_attempt_at"])


def _blob_tables() -> None:
    op.add_column("chat_files", sa.Column("content_hash", sa.String(64), nullable=True))
    op.add_column("chat_files", sa.Column("storage_version", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_chat_files_content_hash", "chat_files", ["content_hash"])
    op.create_table(
        "dr_blob_manifests",
        sa.Column("content_hash", sa.String(64), primary_key=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("local_path", sa.String(512), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False, unique=True),
        sa.Column("object_version_id", sa.String(255)),
        sa.Column("object_etag", sa.String(255)),
        sa.Column("state", sa.String(16), nullable=False, server_default="local"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("uploaded_at", sa.DateTime(timezone=True)),
        sa.Column("tombstoned_at", sa.DateTime(timezone=True)),
        sa.Column("retain_until", sa.DateTime(timezone=True)),
        sa.Column("local_deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("size_bytes >= 0", name="ck_dr_blob_manifest_size"),
        sa.CheckConstraint("state IN ('local', 'uploaded', 'tombstoned')", name="ck_dr_blob_manifest_state"),
    )
    op.create_table(
        "dr_file_intents",
        sa.Column("intent_id", sa.String(36), primary_key=True),
        sa.Column("chat_file_id", sa.String(36), sa.ForeignKey("chat_files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("content_hash", sa.String(64), sa.ForeignKey("dr_blob_manifests.content_hash", ondelete="RESTRICT"), nullable=False),
        sa.Column("origin_physical_site", sa.String(16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chat_file_id", name="ux_dr_file_intents_chat_file"),
        sa.CheckConstraint("writer_epoch >= 1", name="ck_dr_file_intents_writer_epoch"),
    )
    op.create_table(
        "dr_blob_deliveries",
        sa.Column("content_hash", sa.String(64), sa.ForeignKey("dr_blob_manifests.content_hash", ondelete="RESTRICT"), primary_key=True),
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending_upload"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledgement_hash", sa.String(64)),
        sa.Column("last_error_code", sa.String(64)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('pending_upload', 'available', 'acknowledged', 'failed', 'quarantined')", name="ck_dr_blob_deliveries_status"),
    )
    op.create_index("ix_dr_blob_deliveries_ready", "dr_blob_deliveries", ["destination_site", "status", "next_attempt_at"])
    op.create_table(
        "dr_blob_receipts",
        sa.Column("content_hash", sa.String(64), primary_key=True),
        sa.Column("destination_site", sa.String(16), primary_key=True),
        sa.Column("origin_physical_site", sa.String(16), primary_key=True),
        sa.Column("object_version_id", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("local_path", sa.String(512), nullable=False),
        sa.Column("receipt_hash", sa.String(64), nullable=False),
        sa.Column("source_acknowledgement_hash", sa.String(64)),
        sa.Column("reported_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "dr_recovery_manifests",
        sa.Column("manifest_id", sa.String(36), primary_key=True),
        sa.Column("manifest_kind", sa.String(16), nullable=False),
        sa.Column("physical_site", sa.String(16), nullable=False),
        sa.Column("writer_epoch", sa.BigInteger(), nullable=False),
        sa.Column("release_sha", sa.String(64), nullable=False),
        sa.Column("database_lsn", sa.String(64), nullable=False),
        sa.Column("database_snapshot", sa.String(255), nullable=False),
        sa.Column("database_fingerprint_hash", sa.String(64), nullable=False),
        sa.Column("database_row_count", sa.BigInteger(), nullable=False),
        sa.Column("event_checkpoint_hash", sa.String(64), nullable=False),
        sa.Column("blob_set_hash", sa.String(64), nullable=False),
        sa.Column("blob_count", sa.BigInteger(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('prepared', 'verified', 'invalidated')", name="ck_dr_recovery_manifests_status"),
        sa.CheckConstraint("manifest_kind IN ('promotion', 'origin')", name="ck_dr_recovery_manifests_kind"),
    )
    op.create_table(
        "dr_durability_state",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("connectivity_mode", sa.String(16), nullable=False, server_default="ambiguous"),
        sa.Column("event_journal_healthy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blob_journal_healthy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_hash", sa.String(64)),
        sa.Column("evidence_expires_at", sa.DateTime(timezone=True)),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("singleton_id = 1", name="ck_dr_durability_state_singleton"),
        sa.CheckConstraint("connectivity_mode IN ('online', 'isolated', 'ambiguous')", name="ck_dr_durability_state_mode"),
    )
    op.execute(
        "INSERT INTO dr_durability_state (singleton_id, connectivity_mode, "
        "event_journal_healthy, blob_journal_healthy, updated_by) "
        "VALUES (1, 'ambiguous', false, false, 'migration')"
    )


def _database_fence() -> None:
    op.create_table(
        "dr_database_runtime",
        sa.Column("singleton_id", sa.Integer(), primary_key=True),
        sa.Column("enforcement_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("physical_site", sa.String(16)),
        sa.Column("application_role", sa.String(63)),
        sa.Column("projection_role", sa.String(63)),
        sa.Column("control_role", sa.String(63)),
        sa.Column("require_witness_lease", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("singleton_id = 1", name="ck_dr_database_runtime_singleton"),
        sa.CheckConstraint("physical_site IS NULL OR physical_site IN ('webapp_fi', 'webapp_ir')", name="ck_dr_database_runtime_site"),
    )
    op.execute("INSERT INTO dr_database_runtime (singleton_id, enforcement_enabled, updated_by) VALUES (1, false, 'migration')")
    op.create_table(
        "dr_projection_table_allowlist",
        sa.Column("table_name", sa.String(64), primary_key=True),
    )
    for table_name in SYNC_PROJECTION_TABLES + LOCAL_PROJECTION_TABLES + WEBAPP_DR_PROJECTION_TABLES:
        op.execute(sa.text("INSERT INTO dr_projection_table_allowlist (table_name) VALUES (:name)").bindparams(name=table_name))
    op.create_table(
        "dr_projection_field_allowlist",
        sa.Column("table_name", sa.String(64), primary_key=True),
        sa.Column("column_name", sa.String(64), primary_key=True),
    )
    forbidden = {f"{table_name}.{column_name}" for table_name, column_name in PROJECTION_FORBIDDEN_FIELDS}
    for table_name in SYNC_PROJECTION_TABLES + LOCAL_PROJECTION_TABLES + WEBAPP_DR_PROJECTION_TABLES:
        bind = op.get_bind()
        columns = bind.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table_name "
                "ORDER BY ordinal_position"
            ),
            {"table_name": table_name},
        ).scalars().all()
        for column_name in columns:
            if f"{table_name}.{column_name}" in forbidden:
                continue
            op.execute(
                sa.text(
                    "INSERT INTO dr_projection_field_allowlist (table_name, column_name) "
                    "VALUES (:table_name, :column_name)"
                ).bindparams(table_name=table_name, column_name=column_name)
            )
    op.execute(
        """
        CREATE FUNCTION trading_bot_enforce_writer_term() RETURNS trigger
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
                    SELECT 1
                    FROM jsonb_each(to_jsonb(NEW)) AS candidate(column_name, new_value)
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
                OR state_row.witness_lease_expires_at <= clock_timestamp() + interval '15 seconds'
            ) THEN
                RAISE EXCEPTION 'three-site writer witness lease is stale';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table_name in WRITER_FENCED_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_three_site_writer_term BEFORE INSERT OR UPDATE OR DELETE ON {table_name} "
            "FOR EACH ROW EXECUTE FUNCTION trading_bot_enforce_writer_term()"
        )


def upgrade() -> None:
    _writer_clock_columns()
    _event_tables()
    _blob_tables()
    op.execute(
        """
        CREATE FUNCTION trading_bot_dr_event_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'dr_events are immutable';
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_dr_events_immutable BEFORE UPDATE OR DELETE ON dr_events "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_event_immutable()"
    )
    op.execute(
        """
        CREATE FUNCTION trading_bot_dr_effect_intent_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            IF NEW.effect_id IS DISTINCT FROM OLD.effect_id
               OR NEW.event_id IS DISTINCT FROM OLD.event_id
               OR NEW.origin_physical_site IS DISTINCT FROM OLD.origin_physical_site
               OR NEW.executor_site IS DISTINCT FROM OLD.executor_site
               OR NEW.writer_epoch IS DISTINCT FROM OLD.writer_epoch
               OR NEW.effect_type IS DISTINCT FROM OLD.effect_type
               OR NEW.provider IS DISTINCT FROM OLD.provider
               OR NEW.destination_key_hash IS DISTINCT FROM OLD.destination_key_hash
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.payload::jsonb IS DISTINCT FROM OLD.payload::jsonb
               OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'dr_effect_outbox immutable intent fields changed';
            END IF;
            RETURN NEW;
        END; $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_dr_effect_intent_immutable BEFORE UPDATE ON dr_effect_outbox "
        "FOR EACH ROW EXECUTE FUNCTION trading_bot_dr_effect_intent_immutable()"
    )
    _database_fence()


def downgrade() -> None:
    for table_name in reversed(WRITER_FENCED_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_three_site_writer_term ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_enforce_writer_term()")
    op.drop_table("dr_projection_field_allowlist")
    op.drop_table("dr_projection_table_allowlist")
    op.drop_table("dr_database_runtime")
    op.execute("DROP TRIGGER IF EXISTS trg_dr_events_immutable ON dr_events")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_event_immutable()")
    op.execute("DROP TRIGGER IF EXISTS trg_dr_effect_intent_immutable ON dr_effect_outbox")
    op.execute("DROP FUNCTION IF EXISTS trading_bot_dr_effect_intent_immutable()")
    op.drop_index("ix_dr_effect_outbox_ready", table_name="dr_effect_outbox")
    op.drop_table("dr_effect_outbox")
    op.drop_table("dr_replay_nonces")
    op.drop_index("ix_dr_conflict_unresolved", table_name="dr_conflict_quarantine")
    op.drop_table("dr_conflict_quarantine")
    op.drop_table("dr_stream_checkpoints")
    op.drop_table("dr_projection_versions")
    op.drop_table("dr_event_receipts")
    op.drop_index("ix_dr_event_deliveries_ready", table_name="dr_event_deliveries")
    op.drop_table("dr_event_deliveries")
    op.drop_index("ix_dr_events_stream", table_name="dr_events")
    op.drop_index("ix_dr_events_aggregate", table_name="dr_events")
    op.drop_table("dr_events")
    op.drop_table("dr_producer_cursors")
    op.drop_table("dr_durability_state")
    op.drop_table("dr_recovery_manifests")
    op.drop_table("dr_blob_receipts")
    op.drop_index("ix_dr_blob_deliveries_ready", table_name="dr_blob_deliveries")
    op.drop_table("dr_blob_deliveries")
    op.drop_table("dr_file_intents")
    op.drop_table("dr_blob_manifests")
    op.drop_index("ix_chat_files_content_hash", table_name="chat_files")
    op.drop_column("chat_files", "storage_version")
    op.drop_column("chat_files", "content_hash")
    for column in (
        "witness_clock_offset_ms", "witness_observed_boottime", "witness_observed_wall_at",
        "witness_local_boottime_deadline", "witness_local_boot_id", "witness_lease_issued_at",
    ):
        op.drop_column("webapp_writer_state", column)
