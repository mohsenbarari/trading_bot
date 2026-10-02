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


class RecentInventoryContinuationTenToggleContractTests(unittest.TestCase):
    def _assert_schema_version(self, data: dict) -> None:
        self.assertIn("schema_version", data, "schema_version is missing")
        schema_version = data["schema_version"]
        self.assertIs(
            type(schema_version),
            int,
            f"schema_version must be exact builtin int, got {type(schema_version).__name__}",
        )
        self.assertEqual(
            schema_version,
            1,
            f"schema_version must equal 1, got {schema_version!r}",
        )

    def test_toggle_selected_record_exact_provenance_and_coverage(self):
        repo_root = Path(__file__).resolve().parent.parent

        continuation_10_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json"
        )
        continuation_10_path = repo_root / continuation_10_rel_path
        self.assertTrue(
            continuation_10_path.is_file(),
            f"Continuation 10 not found at {continuation_10_path}",
        )

        continuation_10_bytes = continuation_10_path.read_bytes()
        continuation_10_data = json.loads(continuation_10_bytes.decode("utf-8"))

        self._assert_schema_version(continuation_10_data)
        self.assertEqual(continuation_10_data.get("stage"), "P1-00b")
        self.assertEqual(
            set(continuation_10_data.keys()),
            {"schema_version", "stage", "predecessor_inventory", "records"},
        )

        predecessor_binding = continuation_10_data.get("predecessor_inventory", {})
        expected_predecessor_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json"
        )
        expected_predecessor_sha = (
            "7dd8751b91e53fb47161197a16401df8872446aad1f272c0aaa01b3e0efb82d6"
        )
        expected_predecessor_commit = "d90a545db84dc5d3d2cc8ab6f0b4cd3243ade0a8"

        self.assertEqual(predecessor_binding.get("path"), expected_predecessor_path)
        self.assertEqual(predecessor_binding.get("sha256"), expected_predecessor_sha)
        self.assertEqual(predecessor_binding.get("last_commit"), expected_predecessor_commit)
        self.assertEqual(
            set(predecessor_binding.keys()),
            {"path", "sha256", "last_commit"},
        )

        continuation_9_path = repo_root / expected_predecessor_path
        self.assertTrue(
            continuation_9_path.is_file(),
            f"Continuation 9 not found at {continuation_9_path}",
        )
        continuation_9_bytes = continuation_9_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(continuation_9_bytes).hexdigest(),
            expected_predecessor_sha,
        )

        records = continuation_10_data.get("records", [])
        self.assertEqual(len(records), 1)
        record = records[0]

        expected_record_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
        self.assertEqual(set(record.keys()), expected_record_keys)

        expected_test_path = "tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py"
        expected_test_sha = "874fd169c5bd5931e61c32696762b9c1cc3ea6daf6cb76dce2164b97308a930b"
        expected_last_commit = "3f7933fc134303fdc09837550be931eafac4c0a2"
        expected_seed_id = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334"
        expected_test_selectors = [
            "tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py::AdminBroadcastSelectedUserRejectionCharacterizationTests::test_malformed_target_alerts_without_state_or_delivery",
            "tests/test_p1_00b_characterization_admin_broadcast_selected_user_rejection.py::AdminBroadcastSelectedUserRejectionCharacterizationTests::test_new_target_at_cap_alerts_without_mutation_or_delivery",
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
        self.assertEqual(target_entry["handler"], "admin_broadcast.toggle_selected_user")
        self.assertEqual(target_entry["source"], "bot/handlers/admin_broadcast.py:334")

    def test_schema_version_rejects_invalid_types_and_values_with_specific_failure(self):
        repo_root = Path(__file__).resolve().parent.parent
        continuation_10_rel_path = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json"
        )
        continuation_10_path = repo_root / continuation_10_rel_path
        canonical_data = json.loads(continuation_10_path.read_bytes().decode("utf-8"))

        self._assert_schema_version(canonical_data)
        self._assert_schema_version(dict(canonical_data, schema_version=1))

        with self.assertRaisesRegex(AssertionError, "schema_version must be exact builtin int"):
            self._assert_schema_version(dict(canonical_data, schema_version=True))

        with self.assertRaisesRegex(AssertionError, "schema_version must be exact builtin int"):
            self._assert_schema_version(dict(canonical_data, schema_version=False))

        with self.assertRaisesRegex(AssertionError, "schema_version must be exact builtin int"):
            self._assert_schema_version(dict(canonical_data, schema_version=1.0))

        with self.assertRaisesRegex(AssertionError, "schema_version must be exact builtin int"):
            self._assert_schema_version(dict(canonical_data, schema_version="1"))

        with self.assertRaisesRegex(AssertionError, "schema_version must be exact builtin int"):
            self._assert_schema_version(dict(canonical_data, schema_version=None))

        missing_data = {k: v for k, v in canonical_data.items() if k != "schema_version"}
        with self.assertRaisesRegex(AssertionError, "schema_version is missing"):
            self._assert_schema_version(missing_data)

        with self.assertRaisesRegex(AssertionError, "schema_version must equal 1"):
            self._assert_schema_version(dict(canonical_data, schema_version=0))

        with self.assertRaisesRegex(AssertionError, "schema_version must equal 1"):
            self._assert_schema_version(dict(canonical_data, schema_version=2))

        class IntSubclass(int):
            pass

        with self.assertRaisesRegex(AssertionError, "schema_version must be exact builtin int"):
            self._assert_schema_version(dict(canonical_data, schema_version=IntSubclass(1)))


if __name__ == "__main__":
    unittest.main()
