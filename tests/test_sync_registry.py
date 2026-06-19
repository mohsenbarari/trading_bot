import unittest

import models  # noqa: F401 - import all model modules into Base.metadata
from core.sync_registry import (
    SyncPolicy,
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
        for table_name in {"change_log", "sync_blocks"}:
            with self.subTest(table_name=table_name):
                self.assertEqual(get_sync_registry_entry(table_name).policy, SyncPolicy.INTERNAL_BOOKKEEPING)

    def test_offer_metadata_future_tables_are_classified_before_migrations_land(self):
        offer_requests = get_sync_registry_entry("offer_requests")
        publication_states = get_sync_registry_entry("offer_publication_states")

        self.assertTrue(offer_requests.planned)
        self.assertEqual(offer_requests.policy, SyncPolicy.SYNC)
        self.assertTrue(publication_states.planned)
        self.assertEqual(publication_states.policy, SyncPolicy.SYNC)


if __name__ == "__main__":
    unittest.main()
