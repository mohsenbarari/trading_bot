import ast
import hashlib
import json
import os
from pathlib import Path
import unittest

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()


class RecentInventoryContinuationEightSearchContractTests(unittest.TestCase):
    def _assert_schema_version(self, data, expected: int = 1):
        if isinstance(data, dict):
            if "schema_version" not in data:
                self.fail("schema_version: missing required field")
            val = data["schema_version"]
        else:
            val = data
        if type(val) is not int:
            self.fail(
                f"schema_version: exact builtin int required, got {type(val).__name__}"
            )
        if val != expected:
            self.fail(f"schema_version: wrong value, expected {expected}, got {val}")

    def test_search_state_record_exact_provenance_and_seed_entry(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_8_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json"
        )
        continuation_8_path = repo_root / continuation_8_rel_path
        self.assertTrue(continuation_8_path.is_file(), f"Continuation 8 not found at {continuation_8_path}")

        continuation_8_bytes = continuation_8_path.read_bytes()
        continuation_8_data = json.loads(continuation_8_bytes.decode("utf-8"))

        self._assert_schema_version(continuation_8_data)
        self.assertEqual(continuation_8_data.get("stage"), "P1-00b")
        self.assertEqual(
            set(continuation_8_data.keys()),
            {"schema_version", "stage", "predecessor_inventory", "records"},
        )

        predecessor_binding = continuation_8_data.get("predecessor_inventory", {})
        expected_predecessor_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json"
        )
        expected_predecessor_sha = (
            "d824f0ba5f2683150fca2e5a690bd0e7c67d9a4e73b8fd2d5a44c35dba78e6b3"
        )
        expected_predecessor_commit = "534e6a419f415f4f208b8621bd37b800941636f1"

        self.assertEqual(predecessor_binding.get("path"), expected_predecessor_path)
        self.assertEqual(predecessor_binding.get("sha256"), expected_predecessor_sha)
        self.assertEqual(predecessor_binding.get("last_commit"), expected_predecessor_commit)

        continuation_7_path = repo_root / expected_predecessor_path
        self.assertTrue(continuation_7_path.is_file(), f"Continuation 7 not found at {continuation_7_path}")
        continuation_7_bytes = continuation_7_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(continuation_7_bytes).hexdigest(),
            expected_predecessor_sha,
        )

        records = continuation_8_data.get("records", [])
        self.assertEqual(len(records), 1)
        record = records[0]

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = "tests/test_p1_00b_characterization_admin_broadcast_search_state.py"
        expected_test_sha = "27fbe303758d108f2e30c1ac0d407126a74641b7d51d65b20864ca0e1e15d295"
        expected_last_commit = "b6ced906adf8a1847bd8962263d1006af55baa62"
        expected_seed_id = "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308"
        expected_test_selectors = [
            "tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_matched_recipients_preserve_selected_marker_and_search_delivery",
            "tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_empty_results_preserve_selected_count_and_no_match_delivery",
            "tests/test_p1_00b_characterization_admin_broadcast_search_state.py::AdminBroadcastSearchStateCharacterizationTests::test_malformed_selected_id_fails_before_session_or_delivery",
        ]

        self.assertEqual(record["path"], expected_test_path)
        self.assertEqual(record["sha256"], expected_test_sha)
        self.assertEqual(record["last_commit"], expected_last_commit)
        self.assertEqual(record["seed_ids"], [expected_seed_id])
        self.assertEqual(record["test_selectors"], expected_test_selectors)

        test_file_path = repo_root / expected_test_path
        self.assertTrue(test_file_path.is_file(), f"Test file not found at {test_file_path}")
        test_file_bytes = test_file_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(test_file_bytes).hexdigest(),
            expected_test_sha,
            f"SHA256 mismatch for {expected_test_path}",
        )

        test_file_text = test_file_bytes.decode("utf-8")
        parsed_ast = ast.parse(test_file_text, filename=str(test_file_path))
        class_nodes = {
            node.name: node
            for node in parsed_ast.body
            if isinstance(node, ast.ClassDef)
        }

        for selector in expected_test_selectors:
            parts = selector.split("::")
            self.assertEqual(len(parts), 3)
            self.assertEqual(parts[0], expected_test_path)
            cls_name = parts[1]
            method_name = parts[2]
            self.assertIn(cls_name, class_nodes, f"Class {cls_name} not found in AST")
            cls_node = class_nodes[cls_name]
            method_names = {
                subnode.name
                for subnode in cls_node.body
                if isinstance(subnode, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            self.assertIn(method_name, method_names, f"Method {method_name} not found in {cls_name}")

        surface_bot_path = repo_root / "docs/refactor/two-server/section-1/inventory/surface-bot.json"
        self.assertTrue(surface_bot_path.is_file(), f"surface-bot.json not found at {surface_bot_path}")
        surface_bot_data = json.loads(surface_bot_path.read_bytes().decode("utf-8"))
        matching_entries = [
            entry
            for entry in surface_bot_data.get("entries", [])
            if entry.get("behavior_id") == expected_seed_id
        ]
        self.assertEqual(
            len(matching_entries),
            1,
            f"Expected exactly one entry for seed {expected_seed_id}, found {len(matching_entries)}",
        )

        target_entry = matching_entries[0]
        self.assertEqual(target_entry["handler"], "admin_broadcast.process_selected_recipient_search")
        self.assertEqual(target_entry["source"], "bot/handlers/admin_broadcast.py:308")

    def test_schema_version_rejects_invalid_types_and_values_with_specific_failure(self):
        repo_root = Path(__file__).resolve().parent.parent
        continuation_8_path = (
            repo_root
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json"
        )
        self.assertTrue(
            continuation_8_path.is_file(),
            f"Continuation 8 not found at {continuation_8_path}",
        )

        canonical_bytes = continuation_8_path.read_bytes()
        canonical_data = json.loads(canonical_bytes.decode("utf-8"))

        # Positively validate canonical JSON and builtin int 1
        self._assert_schema_version(canonical_data)
        canonical_with_builtin_int_1 = dict(canonical_data)
        canonical_with_builtin_int_1["schema_version"] = int(1)
        self._assert_schema_version(canonical_with_builtin_int_1)
        self._assert_schema_version(1)

        # Missing field
        missing_field_data = dict(canonical_data)
        del missing_field_data["schema_version"]
        with self.assertRaisesRegex(AssertionError, r"schema_version: missing required field"):
            self._assert_schema_version(missing_field_data)

        # Non-exact builtin integer types
        class _IntSubclass(int):
            pass

        invalid_type_cases = [
            (True, "bool"),
            (False, "bool"),
            (1.0, "float"),
            ("1", "str"),
            (None, "NoneType"),
            (_IntSubclass(1), "_IntSubclass"),
        ]
        for bad_val, type_name in invalid_type_cases:
            corrupt_data = dict(canonical_data)
            corrupt_data["schema_version"] = bad_val
            with self.assertRaisesRegex(
                AssertionError,
                rf"schema_version: exact builtin int required, got {type_name}",
            ):
                self._assert_schema_version(corrupt_data)

        # Wrong builtin integer values
        for wrong_val in (0, 2):
            corrupt_data = dict(canonical_data)
            corrupt_data["schema_version"] = wrong_val
            with self.assertRaisesRegex(
                AssertionError,
                rf"schema_version: wrong value, expected 1, got {wrong_val}",
            ):
                self._assert_schema_version(corrupt_data)

        self.assertEqual(continuation_8_path.read_bytes(), canonical_bytes)


if __name__ == "__main__":
    unittest.main()
