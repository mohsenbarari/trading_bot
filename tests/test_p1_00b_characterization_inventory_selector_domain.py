"""Characterization tests for inventory test-selector domain rejection in assert_record_evidence.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161
Canonical Open Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Scenarios:
- Scenario 1 (test_two_component_selector_rejected): Two-component path::Class malformed selector
  rejected by assert_record_evidence with branch-specific path::Class::method format message.
- Scenario 2 (test_async_method_selector_rejected): Three-component selector
  path::StrictEmptyListAsyncSession::execute resolving to an actual AsyncFunctionDef rejected by
  assert_record_evidence with explicit synchronous-found-async assertion error.

Exclusions:
- Excludes already accepted missing-class/missing-method scenarios at 06ad31a37619bd9cc650cbb7f42a6b001fc568ce.
- Excludes helper path/hash-mismatch scenarios at 73ef5f046fa99723467ae1b669e96631c61449ef.
- Excludes HTTP runtime, authentication, and authorization policy.
- Excludes read_user self-success and super-admin rejection.
- Excludes mutation, persistence, retry, reload, origin matrix, ledger/inventory/checkpoint/count edits,
  sixth behavior acceptance, and any seed/blocker/Stage closure.
"""

import ast
import hashlib
import json
from pathlib import Path
import re
import unittest

from tests.test_p1_00b_characterization_inventory_evidence_helper import (
    assert_record_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Authoritative literal C15 oracle constants (independently frozen, not imported from helper)
C15_INVENTORY_REL_PATH = Path(
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
)
C15_INVENTORY_SHA256 = (
    "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
)
C15_RECORD_PATH = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
)
C15_RECORD_SHA256 = (
    "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
)
C15_LAST_COMMIT = "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8"
C15_SEED_IDS = ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"]
C15_GENUINE_SELECTOR = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
    "::UsersReadAllEmptyHttpCharacterizationTests"
    "::test_empty_list_returns_http_200_without_relation_queries"
)

HELPER_REL_PATH = Path(
    "tests/test_p1_00b_characterization_inventory_evidence_helper.py"
)
HELPER_SHA256 = (
    "522af0f05ca6d83c19d2f36581d53f7cc85a5d27d10dff4b00a290d4e2e88c4c"
)


class InventorySelectorDomainTests(unittest.TestCase):
    """Independent selector domain rejection characterization tests using C15 evidence."""

    def _read_and_assert_genuine_oracle(self) -> tuple[bytes, bytes, bytes, dict]:
        inv_path = REPO_ROOT / C15_INVENTORY_REL_PATH
        src_path = REPO_ROOT / C15_RECORD_PATH
        hlp_path = REPO_ROOT / HELPER_REL_PATH

        inv_bytes = inv_path.read_bytes()
        src_bytes = src_path.read_bytes()
        hlp_bytes = hlp_path.read_bytes()

        self.assertEqual(hashlib.sha256(inv_bytes).hexdigest(), C15_INVENTORY_SHA256)
        self.assertEqual(hashlib.sha256(src_bytes).hexdigest(), C15_RECORD_SHA256)
        self.assertEqual(hashlib.sha256(hlp_bytes).hexdigest(), HELPER_SHA256)

        inv_doc = json.loads(inv_bytes.decode("utf-8"))
        self.assertIs(type(inv_doc["schema_version"]), int)
        self.assertEqual(inv_doc["schema_version"], 1)
        self.assertEqual(inv_doc["stage"], "P1-00b")

        records = inv_doc["records"]
        self.assertEqual(len(records), 1)

        record = records[0]
        self.assertEqual(record["path"], C15_RECORD_PATH)
        self.assertEqual(record["sha256"], C15_RECORD_SHA256)
        self.assertEqual(record["last_commit"], C15_LAST_COMMIT)
        self.assertEqual(record["seed_ids"], C15_SEED_IDS)
        self.assertEqual(record["test_selectors"], [C15_GENUINE_SELECTOR])

        assert_record_evidence(self, record, REPO_ROOT)
        return inv_bytes, src_bytes, hlp_bytes, record

    def test_two_component_selector_rejected(self) -> None:
        """Verify rejection when selector has only two components (path::Class)."""
        inv_bytes_before, src_bytes_before, hlp_bytes_before, genuine_record = (
            self._read_and_assert_genuine_oracle()
        )

        two_component_selector = (
            f"{C15_RECORD_PATH}::UsersReadAllEmptyHttpCharacterizationTests"
        )
        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": [two_component_selector],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        self.assertIsNot(decoded_mutant, genuine_record)
        self.assertIsNot(
            decoded_mutant["test_selectors"],
            genuine_record["test_selectors"],
        )
        self.assertIsNot(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(set(decoded_mutant.keys()), set(genuine_record.keys()))
        changed_keys = {
            k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]
        }
        self.assertEqual(changed_keys, {"test_selectors"})

        self.assertEqual(decoded_mutant["path"], C15_RECORD_PATH)
        self.assertEqual(decoded_mutant["sha256"], C15_RECORD_SHA256)
        self.assertEqual(decoded_mutant["last_commit"], genuine_record["last_commit"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])

        parts = decoded_mutant["test_selectors"][0].split("::")
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], C15_RECORD_PATH)
        self.assertEqual(parts[1], "UsersReadAllEmptyHttpCharacterizationTests")

        expected_regex = re.escape(
            f"Selector {two_component_selector} must follow path::Class::method format"
        )
        with self.assertRaisesRegex(AssertionError, expected_regex):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        inv_bytes_after = (REPO_ROOT / C15_INVENTORY_REL_PATH).read_bytes()
        src_bytes_after = (REPO_ROOT / C15_RECORD_PATH).read_bytes()
        hlp_bytes_after = (REPO_ROOT / HELPER_REL_PATH).read_bytes()

        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(src_bytes_after, src_bytes_before)
        self.assertEqual(hlp_bytes_after, hlp_bytes_before)

        self._read_and_assert_genuine_oracle()

    def test_async_method_selector_rejected(self) -> None:
        """Verify rejection when selector targets an AsyncFunctionDef method."""
        inv_bytes_before, src_bytes_before, hlp_bytes_before, genuine_record = (
            self._read_and_assert_genuine_oracle()
        )

        three_component_async_selector = (
            f"{C15_RECORD_PATH}::StrictEmptyListAsyncSession::execute"
        )
        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": [three_component_async_selector],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        self.assertIsNot(decoded_mutant, genuine_record)
        self.assertIsNot(
            decoded_mutant["test_selectors"],
            genuine_record["test_selectors"],
        )
        self.assertIsNot(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(set(decoded_mutant.keys()), set(genuine_record.keys()))
        changed_keys = {
            k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]
        }
        self.assertEqual(changed_keys, {"test_selectors"})

        self.assertEqual(decoded_mutant["path"], C15_RECORD_PATH)
        self.assertEqual(decoded_mutant["sha256"], C15_RECORD_SHA256)
        self.assertEqual(decoded_mutant["last_commit"], genuine_record["last_commit"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])

        parts = decoded_mutant["test_selectors"][0].split("::")
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[0], C15_RECORD_PATH)
        self.assertEqual(parts[1], "StrictEmptyListAsyncSession")
        self.assertEqual(parts[2], "execute")

        source_path = REPO_ROOT / C15_RECORD_PATH
        source_bytes = source_path.read_bytes()
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), C15_RECORD_SHA256)
        tree = ast.parse(source_bytes.decode("utf-8"), filename=str(source_path))

        class_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "StrictEmptyListAsyncSession":
                class_node = node
                break
        self.assertIsNotNone(
            class_node,
            "Top-level class StrictEmptyListAsyncSession not found in AST",
        )

        async_exec_nodes = [
            node
            for node in class_node.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute"
        ]
        sync_exec_nodes = [
            node
            for node in class_node.body
            if isinstance(node, ast.FunctionDef) and node.name == "execute"
        ]
        self.assertEqual(len(async_exec_nodes), 1)
        self.assertEqual(len(sync_exec_nodes), 0)

        expected_regex = (
            r"Selector method execute in StrictEmptyListAsyncSession "
            r"must be synchronous, found async"
        )
        with self.assertRaisesRegex(AssertionError, expected_regex):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        inv_bytes_after = (REPO_ROOT / C15_INVENTORY_REL_PATH).read_bytes()
        src_bytes_after = (REPO_ROOT / C15_RECORD_PATH).read_bytes()
        hlp_bytes_after = (REPO_ROOT / HELPER_REL_PATH).read_bytes()

        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(src_bytes_after, src_bytes_before)
        self.assertEqual(hlp_bytes_after, hlp_bytes_before)

        self._read_and_assert_genuine_oracle()


if __name__ == "__main__":
    unittest.main()
