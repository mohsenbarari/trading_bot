"""Hermetic stdlib inventory-record selector resolution characterization tests.

Stage: P1-00b
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161
Canonical gap: docs/refactor/two-server/section-1/00-stage-ledger.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Helper-consumer contract:
- Consumes accepted assert_record_evidence(test_case, record, repository_root) from
  tests.test_p1_00b_characterization_inventory_evidence_helper.
- Exercises AST selector resolution rejection branches against genuine C15 continuation inventory.

Scenarios:
- Scenario 1 (test_missing_top_level_class_rejected): Proves helper rejects selector when the specified
  top-level class is absent from the parsed source AST.
- Scenario 2 (test_missing_synchronous_method_rejected): Proves helper rejects selector when the specified
  synchronous method is absent from the parsed class AST.

Exclusions:
- Excludes ledger, inventory, count edits, or Stage closure.
- Excludes sixth behavior acceptance or seed-handler batching.
- Excludes HTTP runtime, network, database Session, models, or persistence execution.
- Excludes mutation, retry, reload, and origin matrix.
- Excludes read_user self-success or super-admin rejection.
"""

import ast
import hashlib
import json
from pathlib import Path
import unittest

from tests.test_p1_00b_characterization_inventory_evidence_helper import (
    assert_record_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Independently frozen local literal C15 oracle constants (not imported from helper)
C15_INVENTORY_REL_PATH = Path(
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
)
PINNED_C15_INVENTORY_SHA256 = (
    "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
)
PINNED_C15_RECORD_PATH = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
)
PINNED_C15_RECORD_SHA256 = (
    "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
)
PINNED_C15_RECORD_LAST_COMMIT = "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8"
PINNED_C15_RECORD_SEED_IDS = ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"]
PINNED_C15_RECORD_SELECTOR = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
    "::UsersReadAllEmptyHttpCharacterizationTests"
    "::test_empty_list_returns_http_200_without_relation_queries"
)

HELPER_REL_PATH = Path(
    "tests/test_p1_00b_characterization_inventory_evidence_helper.py"
)
PINNED_HELPER_SHA256 = (
    "522af0f05ca6d83c19d2f36581d53f7cc85a5d27d10dff4b00a290d4e2e88c4c"
)


class InventorySelectorResolutionTests(unittest.TestCase):
    """Characterization tests for inventory record AST selector resolution rejection branches."""

    def _read_inventory_bytes(self) -> bytes:
        return (REPO_ROOT / C15_INVENTORY_REL_PATH).read_bytes()

    def _read_source_bytes(self) -> bytes:
        return (REPO_ROOT / PINNED_C15_RECORD_PATH).read_bytes()

    def _read_helper_bytes(self) -> bytes:
        return (REPO_ROOT / HELPER_REL_PATH).read_bytes()

    def _read_genuine_c15_record(self) -> tuple[bytes, dict]:
        raw_bytes = self._read_inventory_bytes()
        data = json.loads(raw_bytes.decode("utf-8"))
        return raw_bytes, data["records"][0]

    def test_missing_top_level_class_rejected(self) -> None:
        """Verify helper rejects selector referencing an absent top-level class."""
        inv_bytes_before = self._read_inventory_bytes()
        source_bytes_before = self._read_source_bytes()
        helper_bytes_before = self._read_helper_bytes()

        self.assertEqual(
            hashlib.sha256(inv_bytes_before).hexdigest(),
            PINNED_C15_INVENTORY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(source_bytes_before).hexdigest(),
            PINNED_C15_RECORD_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(helper_bytes_before).hexdigest(),
            PINNED_HELPER_SHA256,
        )

        genuine_record = json.loads(inv_bytes_before.decode("utf-8"))["records"][0]
        self.assertEqual(genuine_record["path"], PINNED_C15_RECORD_PATH)
        self.assertEqual(genuine_record["sha256"], PINNED_C15_RECORD_SHA256)
        self.assertEqual(genuine_record["last_commit"], PINNED_C15_RECORD_LAST_COMMIT)
        self.assertEqual(genuine_record["seed_ids"], PINNED_C15_RECORD_SEED_IDS)
        self.assertEqual(genuine_record["test_selectors"], [PINNED_C15_RECORD_SELECTOR])

        # Validate genuine committed record before mutation
        assert_record_evidence(self, genuine_record, REPO_ROOT)

        # AST-only absence check on genuine source file without import or execution
        tree = ast.parse(
            source_bytes_before.decode("utf-8"),
            filename=str(REPO_ROOT / PINNED_C15_RECORD_PATH),
        )
        top_level_classes = [
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        ]
        self.assertIn("UsersReadAllEmptyHttpCharacterizationTests", top_level_classes)

        absent_class_name = "NonexistentTopLevelCharacterizationTests"
        self.assertNotIn(absent_class_name, top_level_classes)

        # Synthesize mutant with absent class name, keeping exact source path and synchronous method
        mutant_selector = (
            f"{PINNED_C15_RECORD_PATH}"
            f"::{absent_class_name}"
            "::test_empty_list_returns_http_200_without_relation_queries"
        )
        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": [mutant_selector],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        # Prove independent object and selector-list identity
        self.assertIsNot(decoded_mutant, genuine_record)
        self.assertIsNot(decoded_mutant["test_selectors"], genuine_record["test_selectors"])

        # Prove unchanged fields and that only test_selectors changed
        self.assertEqual(decoded_mutant["path"], genuine_record["path"])
        self.assertEqual(decoded_mutant["sha256"], genuine_record["sha256"])
        self.assertEqual(decoded_mutant["last_commit"], genuine_record["last_commit"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertNotEqual(decoded_mutant["test_selectors"], genuine_record["test_selectors"])
        diff_keys = [k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]]
        self.assertEqual(diff_keys, ["test_selectors"])

        # Prove selector path matches record path
        selector_parts = decoded_mutant["test_selectors"][0].split("::")
        self.assertEqual(len(selector_parts), 3)
        self.assertEqual(selector_parts[0], decoded_mutant["path"])
        self.assertEqual(selector_parts[1], absent_class_name)
        self.assertEqual(
            selector_parts[2],
            "test_empty_list_returns_http_200_without_relation_queries",
        )

        # Require AssertionError matching the specific Top-level class not-found branch
        expected_error_regex = (
            f"Top-level class {absent_class_name} not found in {PINNED_C15_RECORD_PATH}"
        )
        with self.assertRaisesRegex(AssertionError, expected_error_regex):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        # Assert raw inventory, source, and helper bytes remain unchanged on disk
        inv_bytes_after = self._read_inventory_bytes()
        source_bytes_after = self._read_source_bytes()
        helper_bytes_after = self._read_helper_bytes()
        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(source_bytes_after, source_bytes_before)
        self.assertEqual(helper_bytes_after, helper_bytes_before)
        self.assertEqual(
            hashlib.sha256(inv_bytes_after).hexdigest(),
            PINNED_C15_INVENTORY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(source_bytes_after).hexdigest(),
            PINNED_C15_RECORD_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(helper_bytes_after).hexdigest(),
            PINNED_HELPER_SHA256,
        )

        # Revalidate freshly JSON-decoded genuine disk evidence afterward
        reloaded_record = json.loads(inv_bytes_after.decode("utf-8"))["records"][0]
        self.assertEqual(reloaded_record, genuine_record)
        self.assertIsNot(reloaded_record, genuine_record)
        assert_record_evidence(self, reloaded_record, REPO_ROOT)

    def test_missing_synchronous_method_rejected(self) -> None:
        """Verify helper rejects selector referencing an absent synchronous method."""
        inv_bytes_before = self._read_inventory_bytes()
        source_bytes_before = self._read_source_bytes()
        helper_bytes_before = self._read_helper_bytes()

        self.assertEqual(
            hashlib.sha256(inv_bytes_before).hexdigest(),
            PINNED_C15_INVENTORY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(source_bytes_before).hexdigest(),
            PINNED_C15_RECORD_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(helper_bytes_before).hexdigest(),
            PINNED_HELPER_SHA256,
        )

        genuine_record = json.loads(inv_bytes_before.decode("utf-8"))["records"][0]
        self.assertEqual(genuine_record["path"], PINNED_C15_RECORD_PATH)
        self.assertEqual(genuine_record["sha256"], PINNED_C15_RECORD_SHA256)
        self.assertEqual(genuine_record["last_commit"], PINNED_C15_RECORD_LAST_COMMIT)
        self.assertEqual(genuine_record["seed_ids"], PINNED_C15_RECORD_SEED_IDS)
        self.assertEqual(genuine_record["test_selectors"], [PINNED_C15_RECORD_SELECTOR])

        # Validate genuine committed record before scenario
        assert_record_evidence(self, genuine_record, REPO_ROOT)

        # AST-only absence check on genuine source file without import or execution
        tree = ast.parse(
            source_bytes_before.decode("utf-8"),
            filename=str(REPO_ROOT / PINNED_C15_RECORD_PATH),
        )
        genuine_class_name = "UsersReadAllEmptyHttpCharacterizationTests"
        class_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == genuine_class_name:
                class_node = node
                break
        self.assertIsNotNone(class_node)

        class_methods = [
            node.name
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertIn(
            "test_empty_list_returns_http_200_without_relation_queries",
            class_methods,
        )

        absent_method_name = "test_demonstrably_absent_synchronous_method"
        self.assertNotIn(absent_method_name, class_methods)

        # Synthesize mutant with absent synchronous method, keeping exact source path and class
        mutant_selector = (
            f"{PINNED_C15_RECORD_PATH}"
            f"::{genuine_class_name}"
            f"::{absent_method_name}"
        )
        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": [mutant_selector],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        # Prove independent object and selector-list identity
        self.assertIsNot(decoded_mutant, genuine_record)
        self.assertIsNot(decoded_mutant["test_selectors"], genuine_record["test_selectors"])

        # Prove unchanged fields and that only test_selectors changed
        self.assertEqual(decoded_mutant["path"], genuine_record["path"])
        self.assertEqual(decoded_mutant["sha256"], genuine_record["sha256"])
        self.assertEqual(decoded_mutant["last_commit"], genuine_record["last_commit"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertNotEqual(decoded_mutant["test_selectors"], genuine_record["test_selectors"])
        diff_keys = [k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]]
        self.assertEqual(diff_keys, ["test_selectors"])

        # Prove selector path and class match genuine record
        selector_parts = decoded_mutant["test_selectors"][0].split("::")
        self.assertEqual(len(selector_parts), 3)
        self.assertEqual(selector_parts[0], decoded_mutant["path"])
        self.assertEqual(selector_parts[1], genuine_class_name)
        self.assertEqual(selector_parts[2], absent_method_name)

        # Require AssertionError matching the specific Synchronous method not-found branch
        expected_error_regex = (
            f"Synchronous method {absent_method_name} not found in class {genuine_class_name}"
        )
        with self.assertRaisesRegex(AssertionError, expected_error_regex):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        # Assert raw inventory, source, and helper bytes remain unchanged on disk
        inv_bytes_after = self._read_inventory_bytes()
        source_bytes_after = self._read_source_bytes()
        helper_bytes_after = self._read_helper_bytes()
        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(source_bytes_after, source_bytes_before)
        self.assertEqual(helper_bytes_after, helper_bytes_before)
        self.assertEqual(
            hashlib.sha256(inv_bytes_after).hexdigest(),
            PINNED_C15_INVENTORY_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(source_bytes_after).hexdigest(),
            PINNED_C15_RECORD_SHA256,
        )
        self.assertEqual(
            hashlib.sha256(helper_bytes_after).hexdigest(),
            PINNED_HELPER_SHA256,
        )

        # Revalidate freshly JSON-decoded genuine disk evidence afterward
        reloaded_record = json.loads(inv_bytes_after.decode("utf-8"))["records"][0]
        self.assertEqual(reloaded_record, genuine_record)
        self.assertIsNot(reloaded_record, genuine_record)
        assert_record_evidence(self, reloaded_record, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
