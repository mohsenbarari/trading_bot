import hashlib
import json
from pathlib import Path
import unittest


class RecentCharacterizationContinuationContractTests(unittest.TestCase):
    def test_manifest_indexes_first_two_accepted_units_and_is_sha_bound_to_base_inventory(self):
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
        self.assertEqual(len(records), 2)

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        for record in records:
            self.assertIsInstance(record, dict)
            self.assertEqual(set(record.keys()), expected_record_keys)

        # Validate record 0 (commit 806a0016ad2845a825f91102b88d30fa8a7be7cd)
        expected_record_0 = {
            "path": "tests/test_p1_00b_characterization_admin_broadcast_search_again_unlinked_guard.py",
            "sha256": "e593ed4957839c2feae65d4656bf0b79786390d58da1f2dde35bbac0c043b521",
            "last_commit": "806a0016ad2845a825f91102b88d30fa8a7be7cd",
            "seed_ids": [
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-SEARCH-AGAIN-L377",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_broadcast_search_again_unlinked_guard.py::AdminBroadcastSearchAgainUnlinkedGuardCharacterizationTests::test_search_again_without_user_alerts_before_state_keyboard_or_delivery",
            ],
        }
        self.assertEqual(records[0], expected_record_0)

        # Validate record 1 (commit 845ad843b2d5fcf197bd7715bef3f630f4367287)
        expected_record_1 = {
            "path": "tests/test_p1_00b_characterization_admin_commodities_unlinked_entry_guards.py",
            "sha256": "21b7e771a1d8fb521ef36efd4509ff252da3d4e50a4a981532c9e8c4725b17a3",
            "last_commit": "845ad843b2d5fcf197bd7715bef3f630f4367287",
            "seed_ids": [
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-BACK-TO-LIST-L318",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-MANAGE-ALIASES-L333",
                "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ALIAS-ADD-START-L350",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_entry_guards.py::AdminCommoditiesUnlinkedEntryGuardsCharacterizationTests::test_back_to_list_unlinked_alerts_before_state_navigation_or_delivery",
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_entry_guards.py::AdminCommoditiesUnlinkedEntryGuardsCharacterizationTests::test_manage_aliases_unlinked_alerts_before_parsing_navigation_or_delivery",
                "tests/test_p1_00b_characterization_admin_commodities_unlinked_entry_guards.py::AdminCommoditiesUnlinkedEntryGuardsCharacterizationTests::test_alias_add_start_unlinked_alerts_before_parsing_state_keyboard_or_delivery",
            ],
        }
        self.assertEqual(records[1], expected_record_1)

        # Verify that each recorded SHA-256 equals current bytes of its referenced file
        for record in records:
            char_file_path = repo_root / record["path"]
            self.assertTrue(char_file_path.is_file(), f"Characterization test file not found at {char_file_path}")
            char_bytes = char_file_path.read_bytes()
            char_sha256 = hashlib.sha256(char_bytes).hexdigest()
            self.assertEqual(record["sha256"], char_sha256)


if __name__ == "__main__":
    unittest.main()
