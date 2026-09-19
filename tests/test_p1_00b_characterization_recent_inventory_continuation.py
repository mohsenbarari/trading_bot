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

        # Validate record 0 (commit 69e67edb99dd073ac617e05e06bc69c22bd115df)
        expected_record_0 = {
            "path": "tests/test_p1_00b_characterization_admin_broadcast_unlinked_selection_mutation_guards_batch.py",
            "sha256": "1537d2fd92f15d7ee023af3fd6d5ffbd7d1a113ff06326577c2754a64d6a703b",
            "last_commit": "69e67edb99dd073ac617e05e06bc69c22bd115df",
            "seed_ids": [
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-GROUP-RECIPIENT-L240",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_selection_mutation_guards_batch.py::AdminBroadcastUnlinkedSelectionMutationGuardsCharacterizationTests::test_toggle_group_recipient_without_user_denies_before_payload_state_keyboard_or_delivery",
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_selection_mutation_guards_batch.py::AdminBroadcastUnlinkedSelectionMutationGuardsCharacterizationTests::test_toggle_selected_user_without_user_denies_before_payload_state_session_keyboard_or_delivery",
                "tests/test_p1_00b_characterization_admin_broadcast_unlinked_selection_mutation_guards_batch.py::AdminBroadcastUnlinkedSelectionMutationGuardsCharacterizationTests::test_finish_selected_recipients_without_user_denies_before_state_navigation_or_delivery",
            ],
        }
        self.assertEqual(records[0], expected_record_0)

        # Validate record 1 (commit 8f2c11a69848cb60718e0ac2f0a301b9e52ddee1)
        expected_record_1 = {
            "path": "tests/test_p1_00b_characterization_admin_broadcast_process_selected_recipient_search_unlinked_guard.py",
            "sha256": "da35839509d8ddcb6a6adca44534234e65ea825c41dd3a08373403e7be0b3e95",
            "last_commit": "8f2c11a69848cb60718e0ac2f0a301b9e52ddee1",
            "seed_ids": [
                "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_broadcast_process_selected_recipient_search_unlinked_guard.py::AdminBroadcastProcessSelectedRecipientSearchUnlinkedGuardCharacterizationTests::test_process_selected_recipient_search_without_user_returns_before_message_state_session_or_delivery",
            ],
        }
        self.assertEqual(records[1], expected_record_1)

        # Validate record 2 (commit 6d73e01d1a83009503d21b4eb04e7008e1b89830)
        expected_record_2 = {
            "path": "tests/test_p1_00b_characterization_admin_broadcast_message_unlinked_guards_batch.py",
            "sha256": "93932aef03399991c781c4b16493dcedf6c435200d744eb4ce57c74dcb4a81be",
            "last_commit": "6d73e01d1a83009503d21b4eb04e7008e1b89830",
            "seed_ids": [
                "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-START-TELEGRAM-ADMIN-BROADCAST-L179",
                "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-BROADCAST-MESSAGE-TEXT-L411",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_broadcast_message_unlinked_guards_batch.py::AdminBroadcastMessageUnlinkedGuardsCharacterizationTests::test_start_telegram_admin_broadcast_without_user_returns_before_message_state_keyboard_or_delivery",
                "tests/test_p1_00b_characterization_admin_broadcast_message_unlinked_guards_batch.py::AdminBroadcastMessageUnlinkedGuardsCharacterizationTests::test_process_broadcast_message_text_without_user_returns_before_message_state_validation_session_or_delivery",
            ],
        }
        self.assertEqual(records[2], expected_record_2)

        # Validate record 3 (commit 1d2b8ce0a8637e4317658bdfabbe387cf256b5f1)
        expected_record_3 = {
            "path": "tests/test_p1_00b_characterization_admin_commodities_unlinked_message_guards_batch.py",
            "sha256": "71e64805ae7331aded23c4bb4af9d8ed97dd167a6a31f60b8f3520b87a92cd19",
            "last_commit": "1d2b8ce0a8637e4317658bdfabbe387cf256b5f1",
            "seed_ids": [
                "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ALIAS-ADD-NAME-L365",
                "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ALIAS-EDIT-NAME-L429",
                "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-COMMODITY-EDIT-NAME-L537",
                "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ADD-NAME-L583",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_message_guards_batch.py::AdminCommoditiesUnlinkedMessageGuardsCharacterizationTests::test_alias_add_name_without_user_stops_before_message_state_http_or_delivery",
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_message_guards_batch.py::AdminCommoditiesUnlinkedMessageGuardsCharacterizationTests::test_alias_edit_name_without_user_stops_before_message_state_http_or_delivery",
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_message_guards_batch.py::AdminCommoditiesUnlinkedMessageGuardsCharacterizationTests::test_commodity_edit_name_without_user_stops_before_message_state_http_or_delivery",
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_message_guards_batch.py::AdminCommoditiesUnlinkedMessageGuardsCharacterizationTests::test_add_name_without_user_stops_before_message_state_http_or_delivery",
            ],
        }
        self.assertEqual(records[3], expected_record_3)

        # Validate record 4 (commit 79b577b1a7f9978da1ecdeaccc93284d37cddfa4)
        expected_record_4 = {
            "path": "tests/test_p1_00b_characterization_admin_commodities_unlinked_manage_create_guards_batch.py",
            "sha256": "ce7b87f0194cc748dadc0c9314facefe505cb0bc9d843d7b6903281fa23522d5",
            "last_commit": "79b577b1a7f9978da1ecdeaccc93284d37cddfa4",
            "seed_ids": [
                "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-MANAGE-COMMODITIES-L305",
                "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ADD-ALIASES-AND-CREATE-L622",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_manage_create_guards_batch.py::AdminCommoditiesUnlinkedManageCreateGuardsCharacterizationTests::test_manage_commodities_without_user_stops_before_message_state_navigation_or_delivery",
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_manage_create_guards_batch.py::AdminCommoditiesUnlinkedManageCreateGuardsCharacterizationTests::test_add_aliases_and_create_without_user_stops_before_message_state_http_or_delivery",
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
