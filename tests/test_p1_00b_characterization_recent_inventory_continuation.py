import hashlib
import json
from pathlib import Path
import unittest


class RecentCharacterizationContinuationContractTests(unittest.TestCase):
    def test_manifest_indexes_five_accepted_units_and_is_sha_bound_to_base_inventory(self):
        repo_root = Path(__file__).resolve().parent.parent

        base_inventory_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        base_path = repo_root / base_inventory_rel_path
        self.assertTrue(base_path.is_file(), f"Base inventory not found at {base_path}")

        continuation_rel_path = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json"
        continuation_path = repo_root / continuation_rel_path
        self.assertTrue(continuation_path.is_file(), f"Continuation manifest not found at {continuation_path}")

        base_bytes = base_path.read_bytes()
        calculated_base_sha256 = hashlib.sha256(base_bytes).hexdigest()
        base_data = json.loads(base_bytes.decode("utf-8"))

        continuation_bytes = continuation_path.read_bytes()
        continuation_data = json.loads(continuation_bytes.decode("utf-8"))

        # Validate exact top-level keys
        expected_top_level_keys = {"schema_version", "stage", "base_inventory", "records"}
        self.assertEqual(set(continuation_data.keys()), expected_top_level_keys)

        # Validate top-level schema_version, stage
        self.assertIs(type(continuation_data["schema_version"]), int)
        self.assertEqual(continuation_data["schema_version"], 1)
        self.assertEqual(continuation_data["stage"], "P1-00b")
        self.assertEqual(continuation_data["stage"], base_data["stage"])

        # Validate exact base_inventory keys
        base_inventory_binding = continuation_data["base_inventory"]
        self.assertIsInstance(base_inventory_binding, dict)
        expected_base_inventory_keys = {"path", "sha256", "inspected_head"}
        self.assertEqual(set(base_inventory_binding.keys()), expected_base_inventory_keys)

        # Validate exact binding values
        authoritative_path = "docs/refactor/two-server/section-1/inventory/recent-characterization.json"
        authoritative_sha256 = "5a235244378a63ad9da935e5e70c98593d4664f5f36cfcca3a610743aeef33eb"
        authoritative_head = "5c2b95722ece7b0d22c13ee3c861dfb96a930c92"

        self.assertEqual(base_inventory_binding["path"], authoritative_path)
        self.assertEqual(base_inventory_binding["sha256"], authoritative_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], authoritative_head)

        # Compare binding values to actual base inventory
        self.assertEqual(base_inventory_binding["sha256"], calculated_base_sha256)
        self.assertEqual(base_inventory_binding["inspected_head"], base_data["inspected_head"])

        # Validate records schema and length
        records = continuation_data["records"]
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 5)

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        for record in records:
            self.assertIsInstance(record, dict)
            self.assertEqual(set(record.keys()), expected_record_keys)

        # Validate record 0 (commit 057cdbfb949f702dba8abdedcd1b90b1ab757291)
        expected_record_0 = {
            "path": "tests/test_p1_00b_characterization_trade_create_quick_quantity_manual_prompt.py",
            "sha256": "4881ab2a911ef47aaa177de3ce34ed0c9f93ebc11c1ed201f446b115f2f882c1",
            "last_commit": "057cdbfb949f702dba8abdedcd1b90b1ab757291",
            "seed_ids": [
                "BOT-TRADE-CALLBACK-QUERY-TRADE-CREATE-HANDLE-QUICK-QUANTITY-L869",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_trade_create_quick_quantity_manual_prompt.py::TradeCreateQuickQuantityManualPromptCharacterizationTests::test_manual_value_acknowledges_then_delivers_exact_prompt_without_state_or_settings",
            ],
        }
        self.assertEqual(records[0], expected_record_0)

        # Validate record 1 (commit 5091f773f4e7f6981d4477352af5e86864e6113d)
        expected_record_1 = {
            "path": "tests/test_p1_00b_characterization_admin_users_unlinked_cancel_delete_guards_batch.py",
            "sha256": "d88b7698c030b79a1dff8dc85f7dc1443e951412737950a18e30a8f24ec2eb5a",
            "last_commit": "5091f773f4e7f6981d4477352af5e86864e6113d",
            "seed_ids": [
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-SEARCH-CANCEL-L657",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-DELETE-REQUEST-L1186",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-DELETE-CONFIRM-L1203",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-LIMIT-CANCEL-L1563",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_users_unlinked_cancel_delete_guards_batch.py::AdminUsersUnlinkedCancelDeleteGuardsCharacterizationTests::test_user_search_cancel_without_user_stops_before_state_navigation_or_delivery",
                "tests/test_p1_00b_characterization_admin_users_unlinked_cancel_delete_guards_batch.py::AdminUsersUnlinkedCancelDeleteGuardsCharacterizationTests::test_user_delete_request_without_user_stops_before_parse_session_redirect_or_delivery",
                "tests/test_p1_00b_characterization_admin_users_unlinked_cancel_delete_guards_batch.py::AdminUsersUnlinkedCancelDeleteGuardsCharacterizationTests::test_user_delete_confirm_without_user_stops_before_parse_session_state_navigation_or_delivery",
                "tests/test_p1_00b_characterization_admin_users_unlinked_cancel_delete_guards_batch.py::AdminUsersUnlinkedCancelDeleteGuardsCharacterizationTests::test_limit_cancel_without_user_stops_before_parse_session_state_or_delivery",
            ],
        }
        self.assertEqual(records[1], expected_record_1)

        # Validate record 2 (commit 36716ce745034211472bce5e162c6dffdd163564)
        expected_record_2 = {
            "path": "tests/test_p1_00b_characterization_admin_broadcast_unlinked_navigation_guards_batch.py",
            "sha256": "a72017109eb3527a89ed9f95c017016f291ee392f696960496e903d7551b4764",
            "last_commit": "36716ce745034211472bce5e162c6dffdd163564",
            "seed_ids": [
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CHOOSE-GROUP-RECIPIENTS-L221",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-GROUP-SELECTION-L270",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CHOOSE-SELECTED-RECIPIENTS-L286",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-SEARCH-AGAIN-L377",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_navigation_guards_batch.py::AdminBroadcastUnlinkedNavigationGuardsCharacterizationTests::test_choose_group_recipients_without_user_denies_before_state_keyboard_or_delivery",
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_navigation_guards_batch.py::AdminBroadcastUnlinkedNavigationGuardsCharacterizationTests::test_finish_group_selection_without_user_denies_before_state_navigation_or_delivery",
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_navigation_guards_batch.py::AdminBroadcastUnlinkedNavigationGuardsCharacterizationTests::test_choose_selected_recipients_without_user_denies_before_state_keyboard_or_delivery",
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_navigation_guards_batch.py::AdminBroadcastUnlinkedNavigationGuardsCharacterizationTests::test_search_again_without_user_denies_before_state_navigation_or_delivery",
            ],
        }
        self.assertEqual(records[2], expected_record_2)

        # Validate record 3 (commit 7faf50b9c13a4cc65779c32936375464e79c7cb4)
        expected_record_3 = {
            "path": "tests/test_p1_00b_characterization_publisher_channel_public_trade_callback.py",
            "sha256": "9feeb5b30020fbcef13353f916448894b5aa72b658f2b91f327b5306078f2976",
            "last_commit": "7faf50b9c13a4cc65779c32936375464e79c7cb4",
            "seed_ids": [
                "BOT-TG-CALLBACK-QUERY-TELEGRAM-PUBLISHER-CHANNEL-CALLBACKS-PUBLIC-TRADE-CALLBACK-L46",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_publisher_channel_public_trade_callback.py::PublisherChannelPublicTradeCallbackCharacterizationTests::test_public_trade_callback_forwards_public_identity_and_contention_flags",
                "tests/test_p1_00b_characterization_publisher_channel_public_trade_callback.py::PublisherChannelPublicTradeCallbackCharacterizationTests::test_public_trade_callback_forwards_default_contention_flags",
            ],
        }
        self.assertEqual(records[3], expected_record_3)

        # Validate record 4 (commit bfaed8196419e6701da124bfd6acc677f6b1d245)
        expected_record_4 = {
            "path": "tests/test_p1_00b_characterization_notification_send_test_push_unconfigured.py",
            "sha256": "46e835da03a563aafdb3d52521e59618f421faa138728ee6e63bfa0bd73c95f5",
            "last_commit": "bfaed8196419e6701da124bfd6acc677f6b1d245",
            "seed_ids": [
                "API-NOTIFY-POST-NOTIFICATIONS-SEND-TEST-PUSH-NOTIFICATION-L201",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_notification_send_test_push_unconfigured.py::NotificationSendTestPushUnconfiguredCharacterizationTests::test_unconfigured_push_fails_before_payload_delivery_or_database_work",
            ],
        }
        self.assertEqual(records[4], expected_record_4)

        # Verify that each recorded SHA-256 equals current bytes of its referenced file
        for record in records:
            char_file_path = repo_root / record["path"]
            self.assertTrue(char_file_path.is_file(), f"Characterization test file not found at {char_file_path}")
            char_bytes = char_file_path.read_bytes()
            char_sha256 = hashlib.sha256(char_bytes).hexdigest()
            self.assertEqual(record["sha256"], char_sha256)


if __name__ == "__main__":
    unittest.main()
