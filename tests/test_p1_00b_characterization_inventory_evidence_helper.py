"""Hermetic stdlib inventory-record evidence validation helper and characterization tests.

Stage: P1-00b
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161
Canonical gap: docs/refactor/two-server/section-1/00-stage-ledger.md#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Shared seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

Helper signature & narrow contract:
- assert_record_evidence(test_case, record, repository_root)
- test_case: unittest.TestCase instance providing test assertions
- record: JSON-decoded inventory record dictionary containing path, sha256, and test_selectors
- repository_root: pathlib.Path referencing the repository root directory
- Validates actual source-file raw byte SHA256 against record["sha256"]
- Validates every record["test_selectors"] entry follows exact path::Class::method format
- Validates selector path matches record["path"]
- Parses source AST without importing or executing it; verifies top-level ClassDef and synchronous FunctionDef

Limitations:
- Pure stdlib file-byte and AST inspection only; does not import or execute evidence code.
- Does not invoke Git or external subprocesses.
- Does not validate schema version, predecessor ownership, or seed acceptance.

Observed scenarios:
- Scenario 1 (test_genuine_record_evidence): Pins genuine C15 inventory and record contracts, validating with shared helper.
- Scenario 2 (test_selector_path_mismatch_rejected): Proves helper rejects selector path mismatch with 'selector path' assertion error.
- Scenario 3 (test_source_hash_mismatch_rejected): Proves helper rejects corrupted source SHA256 with 'source SHA256' assertion error.

Exclusions:
- Excludes HTTP runtime and authorization-policy execution.
- Excludes persistence, retry, reload, and origin matrix.
- Excludes ledger, inventory, count edits, new behavior acceptance, or Stage closure.
"""

import ast
import hashlib
import json
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
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
PINNED_C15_RECORD_SELECTOR = (
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
    "::UsersReadAllEmptyHttpCharacterizationTests"
    "::test_empty_list_returns_http_200_without_relation_queries"
)


def assert_record_evidence(
    test_case: unittest.TestCase,
    record: dict,
    repository_root: Path,
) -> None:
    """Validate inventory record source-byte SHA256 and AST test selectors.

    Narrow contract:
    - Validates actual source-byte SHA256 against record["sha256"].
    - Validates every record["test_selectors"] string follows
      path::top-level-Class::synchronous-FunctionDef-method.
    - Validates selector path equals record["path"].
    - Validates top-level ClassDef exists in source AST.
    - Validates synchronous FunctionDef method exists in that ClassDef.
    - Uses explicit unittest assertions on test_case.

    Limitations:
    - Does not import or execute source code.
    - Does not execute git or subprocesses.
    - Does not validate schema version, predecessor ownership, or seed acceptance.
    """
    test_case.assertIsInstance(record, dict)
    test_case.assertIn("path", record)
    test_case.assertIn("sha256", record)
    test_case.assertIn("test_selectors", record)

    source_path = repository_root / record["path"]
    test_case.assertTrue(source_path.is_file(), f"Source file not found: {source_path}")

    source_bytes = source_path.read_bytes()
    actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
    test_case.assertEqual(
        actual_sha256,
        record["sha256"],
        f"Record source SHA256 mismatch for {record['path']}: expected {record['sha256']}, got {actual_sha256}",
    )

    tree = ast.parse(source_bytes.decode("utf-8"), filename=str(source_path))
    test_case.assertIsInstance(record["test_selectors"], list)

    for selector in record["test_selectors"]:
        parts = selector.split("::")
        test_case.assertEqual(
            len(parts),
            3,
            f"Selector {selector} must follow path::Class::method format",
        )
        sel_path, sel_class, sel_method = parts
        test_case.assertEqual(
            sel_path,
            record["path"],
            f"Record selector path mismatch: selector specifies {sel_path}, expected {record['path']}",
        )

        class_node = None
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == sel_class:
                class_node = node
                break
        test_case.assertIsNotNone(
            class_node,
            f"Top-level class {sel_class} not found in {record['path']}",
        )

        method_node = None
        for node in class_node.body:
            if isinstance(node, ast.FunctionDef) and node.name == sel_method:
                method_node = node
                break
            if isinstance(node, ast.AsyncFunctionDef) and node.name == sel_method:
                test_case.fail(
                    f"Selector method {sel_method} in {sel_class} must be synchronous, found async",
                )
        test_case.assertIsNotNone(
            method_node,
            f"Synchronous method {sel_method} not found in class {sel_class}",
        )


class InventoryEvidenceHelperTests(unittest.TestCase):
    """Characterization tests for the reusable inventory record evidence helper."""

    def _read_c15_inventory_bytes(self) -> bytes:
        inv_path = REPO_ROOT / C15_INVENTORY_REL_PATH
        return inv_path.read_bytes()

    def _read_genuine_c15_record(self) -> tuple[bytes, dict]:
        raw_bytes = self._read_c15_inventory_bytes()
        data = json.loads(raw_bytes.decode("utf-8"))
        return raw_bytes, data["records"][0]

    def test_genuine_record_evidence(self) -> None:
        """Verify genuine committed C15 record passes reusable evidence validator."""
        c15_raw_bytes, record = self._read_genuine_c15_record()

        self.assertEqual(
            hashlib.sha256(c15_raw_bytes).hexdigest(),
            PINNED_C15_INVENTORY_SHA256,
        )
        self.assertEqual(record["path"], PINNED_C15_RECORD_PATH)
        self.assertEqual(record["sha256"], PINNED_C15_RECORD_SHA256)
        self.assertEqual(record["last_commit"], PINNED_C15_RECORD_LAST_COMMIT)
        self.assertEqual(record["test_selectors"], [PINNED_C15_RECORD_SELECTOR])

        assert_record_evidence(self, record, REPO_ROOT)

    def test_selector_path_mismatch_rejected(self) -> None:
        """Verify rejection when selector path does not match record path."""
        inv_bytes_before, genuine_record = self._read_genuine_c15_record()
        behavior_path = REPO_ROOT / genuine_record["path"]
        behavior_bytes_before = behavior_path.read_bytes()

        assert_record_evidence(self, genuine_record, REPO_ROOT)

        mismatched_selector = (
            "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py"
            "::UsersReadAllEmptyHttpCharacterizationTests"
            "::test_empty_list_returns_http_200_without_relation_queries"
        )
        mutant_doc = {
            "path": genuine_record["path"],
            "sha256": genuine_record["sha256"],
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": [mismatched_selector],
        }
        mutant_json_text = json.dumps(mutant_doc, indent=2)
        decoded_mutant = json.loads(mutant_json_text)

        self.assertEqual(decoded_mutant["path"], genuine_record["path"])
        self.assertEqual(decoded_mutant["sha256"], genuine_record["sha256"])
        self.assertEqual(decoded_mutant["last_commit"], genuine_record["last_commit"])
        self.assertEqual(decoded_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertNotEqual(decoded_mutant["test_selectors"], genuine_record["test_selectors"])
        diff_keys = [k for k in decoded_mutant if decoded_mutant[k] != genuine_record[k]]
        self.assertEqual(diff_keys, ["test_selectors"])

        sel_path = decoded_mutant["test_selectors"][0].split("::")[0]
        self.assertEqual(
            sel_path,
            "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py",
        )
        self.assertNotEqual(sel_path, decoded_mutant["path"])

        with self.assertRaisesRegex(AssertionError, "selector path"):
            assert_record_evidence(self, decoded_mutant, REPO_ROOT)

        inv_bytes_after = self._read_c15_inventory_bytes()
        behavior_bytes_after = behavior_path.read_bytes()
        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(behavior_bytes_after, behavior_bytes_before)

        reloaded_record = json.loads(inv_bytes_after.decode("utf-8"))["records"][0]
        assert_record_evidence(self, reloaded_record, REPO_ROOT)

    def test_source_hash_mismatch_rejected(self) -> None:
        """Verify rejection when record sha256 does not match actual source bytes."""
        inv_bytes_before, genuine_record = self._read_genuine_c15_record()
        behavior_path = REPO_ROOT / genuine_record["path"]
        behavior_bytes_before = behavior_path.read_bytes()

        assert_record_evidence(self, genuine_record, REPO_ROOT)

        corrupted_hash = "0" * 64
        mutant_hash_doc = {
            "path": genuine_record["path"],
            "sha256": corrupted_hash,
            "last_commit": genuine_record["last_commit"],
            "seed_ids": list(genuine_record["seed_ids"]),
            "test_selectors": list(genuine_record["test_selectors"]),
        }
        mutant_hash_json = json.dumps(mutant_hash_doc, indent=2)
        decoded_hash_mutant = json.loads(mutant_hash_json)

        self.assertEqual(decoded_hash_mutant["path"], genuine_record["path"])
        self.assertEqual(decoded_hash_mutant["last_commit"], genuine_record["last_commit"])
        self.assertEqual(decoded_hash_mutant["seed_ids"], genuine_record["seed_ids"])
        self.assertEqual(decoded_hash_mutant["test_selectors"], genuine_record["test_selectors"])
        diff_keys = [k for k in decoded_hash_mutant if decoded_hash_mutant[k] != genuine_record[k]]
        self.assertEqual(diff_keys, ["sha256"])

        actual_source_sha256 = hashlib.sha256(behavior_bytes_before).hexdigest()
        self.assertNotEqual(decoded_hash_mutant["sha256"], actual_source_sha256)
        self.assertEqual(decoded_hash_mutant["sha256"], corrupted_hash)

        with self.assertRaisesRegex(AssertionError, "source SHA256"):
            assert_record_evidence(self, decoded_hash_mutant, REPO_ROOT)

        inv_bytes_after = self._read_c15_inventory_bytes()
        behavior_bytes_after = behavior_path.read_bytes()
        self.assertEqual(inv_bytes_after, inv_bytes_before)
        self.assertEqual(behavior_bytes_after, behavior_bytes_before)

        reloaded_record = json.loads(inv_bytes_after.decode("utf-8"))["records"][0]
        assert_record_evidence(self, reloaded_record, REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
