from pathlib import Path
import unittest

from core.config import Settings
from core.sync_registry import SyncPolicy, get_sync_registry_entry
from core.telegram_delivery_queue_contract import TelegramDeliveryState
from models.telegram_delivery_job import TelegramDeliveryJobRecord


class TelegramDeliveryJobSchemaTests(unittest.TestCase):
    def test_model_contains_identity_scheduling_fencing_and_audit_fields(self):
        columns = TelegramDeliveryJobRecord.__table__.columns
        required = {
            "id",
            "enqueued_seq",
            "dedupe_key",
            "feeder_kind",
            "feeder_rank",
            "source_natural_id",
            "source_version",
            "action_kind",
            "bot_identity",
            "destination_key",
            "destination_class",
            "method",
            "payload",
            "template_version",
            "payload_hash",
            "priority",
            "priority_rank",
            "delivery_deadline_at",
            "eligible_at",
            "freshness_deadline_at",
            "campaign_id",
            "run_id",
            "state",
            "attempt_count",
            "next_retry_at",
            "worker_id",
            "lease_token",
            "lease_until",
            "dispatch_started_at",
            "provider_ok",
            "provider_status_code",
            "provider_error_code",
            "provider_response",
            "last_retry_after_seconds",
            "last_error_class",
            "last_error_message",
            "outcome_reason",
            "telegram_message_id",
            "sent_at",
            "terminal_at",
            "payload_redacted_at",
            "created_at",
            "updated_at",
        }
        self.assertEqual(required - set(columns.keys()), set())

    def test_model_has_dedupe_identity_and_queue_indexes(self):
        self.assertEqual(
            TelegramDeliveryJobRecord.__table__.columns.dedupe_key.type.length,
            1024,
        )
        unique_constraints = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in TelegramDeliveryJobRecord.__table__.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        }
        self.assertEqual(
            unique_constraints["ux_telegram_delivery_jobs_dedupe_key"],
            ("dedupe_key",),
        )
        self.assertEqual(
            unique_constraints["ux_telegram_delivery_jobs_logical_identity"],
            (
                "feeder_kind",
                "source_natural_id",
                "source_version",
                "action_kind",
                "destination_key",
            ),
        )
        index_names = {index.name for index in TelegramDeliveryJobRecord.__table__.indexes}
        self.assertTrue(
            {
                "ix_telegram_delivery_jobs_claim",
                "ix_telegram_delivery_jobs_lease_recovery",
                "ix_telegram_delivery_jobs_source",
                "ix_telegram_delivery_jobs_campaign",
                "ix_telegram_delivery_jobs_bot_destination_state",
                "ix_telegram_delivery_jobs_run",
            }.issubset(index_names)
        )
        claim_index = next(
            index
            for index in TelegramDeliveryJobRecord.__table__.indexes
            if index.name == "ix_telegram_delivery_jobs_claim"
        )
        self.assertEqual(
            tuple(column.name for column in claim_index.columns),
            (
                "bot_identity",
                "priority",
                "priority_rank",
                "delivery_deadline_at",
                "eligible_at",
                "next_retry_at",
                "enqueued_seq",
            ),
        )
        check_names = {
            constraint.name
            for constraint in TelegramDeliveryJobRecord.__table__.constraints
            if constraint.__class__.__name__ == "CheckConstraint"
        }
        self.assertTrue(
            {
                "ck_telegram_delivery_jobs_bot_identity",
                "ck_telegram_delivery_jobs_editor_route",
            }.issubset(check_names)
        )

    def test_all_contract_states_are_persistable_enum_values(self):
        enum_values = set(TelegramDeliveryJobRecord.__table__.columns.state.type.enums)
        self.assertEqual(enum_values, {state.value for state in TelegramDeliveryState})

    def test_execution_table_is_explicitly_foreign_local_no_sync(self):
        entry = get_sync_registry_entry("telegram_delivery_jobs")
        self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
        self.assertIn("foreign", entry.authority)
        self.assertIn("never cross-sync", entry.conflict_rule)

    def test_feature_flags_default_to_legacy_and_queue_off(self):
        fields = Settings.model_fields
        self.assertEqual(fields["telegram_delivery_execution_owner"].default, "legacy")
        self.assertFalse(fields["telegram_delivery_queue_worker_enabled"].default)
        self.assertFalse(fields["telegram_delivery_queue_cutover_ready"].default)
        self.assertFalse(fields["telegram_delivery_queue_channel_editor_enabled"].default)
        self.assertIsNone(
            fields["telegram_delivery_queue_channel_editor_bot_token"].default
        )
        self.assertIsNone(
            fields["telegram_delivery_queue_expected_primary_bot_id"].default
        )
        self.assertIsNone(
            fields[
                "telegram_delivery_queue_expected_channel_editor_bot_id"
            ].default
        )
        self.assertIsNone(
            fields["telegram_delivery_queue_expected_channel_id"].default
        )
        self.assertEqual(
            fields["telegram_delivery_queue_preflight_timeout_seconds"].default,
            10.0,
        )
        self.assertEqual(
            fields["telegram_delivery_queue_bot_min_interval_seconds"].default,
            0.035,
        )
        self.assertEqual(
            fields["telegram_delivery_queue_destination_min_interval_seconds"].default,
            1.05,
        )
        self.assertEqual(
            fields["telegram_delivery_queue_rate_limit_probe_delay_seconds"].default,
            0.1,
        )

    def test_migration_is_additive_and_points_to_previous_head(self):
        source = Path(
            "migrations/versions/f2c7d8e9a0bd_add_telegram_delivery_jobs.py"
        ).read_text(encoding="utf-8")
        self.assertIn('down_revision: Union[str, Sequence[str], None] = "f1b6e7f8a9dc"', source)
        self.assertIn('"telegram_delivery_jobs"', source)
        self.assertIn('"telegram_delivery_jobs_enqueued_seq_seq"', source)
        self.assertIn('"ux_telegram_delivery_jobs_logical_identity"', source)
        self.assertIn('"ix_telegram_delivery_jobs_claim"', source)
        self.assertIn("postgresql_where", source)
        self.assertNotIn('drop_table("offers")', source)
        self.assertNotIn('drop_table("offer_publication_states")', source)
        self.assertNotIn('drop_table("telegram_notification_outbox")', source)


if __name__ == "__main__":
    unittest.main()
