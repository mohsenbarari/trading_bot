import unittest

import models  # noqa: F401 - import all model modules into Base.metadata
from core.admin_authority import ADMIN_SHARED_TABLES
from core.sync_registry import (
    SyncPolicy,
    admin_mutated_shared_registry_entries,
    get_sync_registry_entry,
    missing_registry_tables,
    model_table_names,
    sync_registry_entries,
)


class SyncRegistryTests(unittest.TestCase):
    def test_registry_covers_every_current_model_table(self):
        missing = missing_registry_tables(model_table_names())
        self.assertEqual(missing, set())

    def test_future_model_table_without_registry_entry_fails_coverage(self):
        missing = missing_registry_tables({"users", "future_product_table"})
        self.assertEqual(missing, {"future_product_table"})

    def test_registry_entries_have_required_policy_metadata(self):
        for table_name, entry in sync_registry_entries(include_planned=True).items():
            with self.subTest(table_name=table_name):
                self.assertEqual(entry.table_name, table_name)
                self.assertIsInstance(entry.policy, SyncPolicy)
                self.assertTrue(entry.write_surfaces)
                self.assertTrue(entry.authority)
                self.assertTrue(entry.conflict_rule)
                self.assertTrue(entry.side_effect_classification)

    def test_messenger_owned_tables_are_no_sync(self):
        for table_name in {
            "messages",
            "conversations",
            "chats",
            "chat_members",
            "chat_files",
            "upload_batches",
            "upload_sessions",
        }:
            with self.subTest(table_name=table_name):
                self.assertEqual(get_sync_registry_entry(table_name).policy, SyncPolicy.NO_SYNC)

    def test_runtime_session_tables_are_no_sync(self):
        for table_name in {
            "user_sessions",
            "session_login_requests",
            "single_session_recovery_requests",
            "single_session_recovery_admin_targets",
        }:
            with self.subTest(table_name=table_name):
                entry = get_sync_registry_entry(table_name)
                self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
                self.assertIn("local", entry.authority)

    def test_telegram_execution_tables_are_foreign_local_no_sync(self):
        for table_name in {
            "telegram_delivery_jobs",
            "telegram_delivery_provider_outcomes",
            "telegram_delivery_reconciliation_evidence",
            "telegram_delivery_runtime_gates",
            "telegram_delivery_feeder_states",
            "telegram_delivery_resume_operations",
            "telegram_interaction_anchor_states",
        }:
            with self.subTest(table_name=table_name):
                entry = get_sync_registry_entry(table_name)
                self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
                self.assertIn("foreign local", entry.authority)

    def test_registration_local_state_tables_are_no_sync(self):
        expected_authority = {
            "invitation_identity_reservations": "iran local",
            "telegram_registration_command_receipts": "iran local",
            "telegram_registration_intents": "foreign local",
        }
        for table_name, authority_fragment in expected_authority.items():
            with self.subTest(table_name=table_name):
                entry = get_sync_registry_entry(table_name)
                self.assertEqual(entry.policy, SyncPolicy.NO_SYNC)
                self.assertIn(authority_fragment, entry.authority)

    def test_user_account_product_fields_are_separate_from_runtime_surface(self):
        users = get_sync_registry_entry("users")

        self.assertEqual(users.policy, SyncPolicy.SYNC)
        self.assertIn("telegram_id", users.side_effect_classification)
        self.assertIn("account status", users.side_effect_classification)
        self.assertIn("role", users.side_effect_classification)
        self.assertIn("limits", users.side_effect_classification)
        self.assertIn("legacy/account-origin compatibility", users.notes)
        self.assertIn("must not represent current active runtime surface", users.notes)

    def test_sync_bookkeeping_tables_are_internal_not_product_sync(self):
        for table_name in {"change_log", "sync_apply_watermarks", "sync_blocks"}:
            with self.subTest(table_name=table_name):
                self.assertEqual(get_sync_registry_entry(table_name).policy, SyncPolicy.INTERNAL_BOOKKEEPING)

    def test_offer_metadata_tables_are_classified_for_sync(self):
        expiry_receipts = get_sync_registry_entry("offer_expiry_command_receipts")
        offer_requests = get_sync_registry_entry("offer_requests")
        publication_states = get_sync_registry_entry("offer_publication_states")

        self.assertEqual(expiry_receipts.policy, SyncPolicy.NO_SYNC)
        self.assertIn("home server", expiry_receipts.authority)
        self.assertFalse(offer_requests.planned)
        self.assertEqual(offer_requests.policy, SyncPolicy.SYNC)
        self.assertEqual(offer_requests.authority, "offer_home_server")
        self.assertFalse(publication_states.planned)
        self.assertEqual(publication_states.policy, SyncPolicy.SYNC)
        self.assertIn("Telegram channel", publication_states.notes)

    def test_admin_mutated_shared_tables_have_single_authority_policy(self):
        entries = admin_mutated_shared_registry_entries()
        self.assertEqual(set(entries), set(ADMIN_SHARED_TABLES))

        for table_name, entry in entries.items():
            with self.subTest(table_name=table_name):
                self.assertEqual(entry.policy, SyncPolicy.SYNC)
                self.assertIn("admin", entry.authority)
                self.assertIn("iran", entry.authority)
                self.assertIn("single-writer iran", entry.conflict_rule)
                self.assertIn("webapp_admin", entry.write_surfaces)

        for table_name in {"commodities", "commodity_aliases", "trading_settings", "users"}:
            with self.subTest(table_name=table_name):
                self.assertIn("telegram_bot_admin", entries[table_name].write_surfaces)


if __name__ == "__main__":
    unittest.main()
