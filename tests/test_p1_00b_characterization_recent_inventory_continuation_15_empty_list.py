"""Provenance characterization test for recent inventory continuation 15 empty list.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-116
Stage: P1-00b (IN_PROGRESS)
Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Gap Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work
Canonical Seed: API-IDENTITY-GET-USERS-READ-ALL-USERS-L407

This module performs provenance-only validation for the users.read_all_users empty list
characterization test (tests/test_p1_00b_characterization_users_read_all_empty_http.py)
chained from continuation-14.
Pending-five=5 remains uncleared (units_since_reconciliation=5), canonical coverage387/465
remains unchanged (135 baseline + 252 new, 78 remaining), Stage P1-00b remains IN_PROGRESS,
and previous continuation-12 remains deferred.
Exclusions: excludes ledger changes, counter edits, seed-handler-coverage edits,
predecessor edits, new behavior acceptance, HTTP/copy/body outcome claims,
custom Session/runtime behavior/model assertions, origin cells, and High-blocker or Stage closure.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent

INVENTORY_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
INVENTORY_PATH = REPO_ROOT / INVENTORY_RELPATH

EXPECTED_SCHEMA_VERSION = 1
EXPECTED_STAGE = "P1-00b"

EXPECTED_PREDECESSOR_PATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json"
EXPECTED_PREDECESSOR_SHA = "782407b237098640fffa0ce1c011d80406ce0b1707b9b06428a5e911e0487554"
EXPECTED_PREDECESSOR_LAST_COMMIT = "540dc1cba9e2efed477cd81b71b946a69b47fcde"

EXPECTED_RECORD_PATH = "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
EXPECTED_RECORD_SHA = "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
EXPECTED_RECORD_LAST_COMMIT = "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8"
EXPECTED_SEED_IDS = ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"]
EXPECTED_TEST_SELECTORS = [
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries"
]


def validate_continuation_fifteen_provenance(
    inventory: dict,
    predecessor_bytes: bytes,
    source_bytes: bytes,
) -> None:
    """Validate exact continuation 15 schema, hashes, and AST selector resolution."""
    expected_top_keys = {"schema_version", "stage", "predecessor_inventory", "records"}
    if set(inventory.keys()) != expected_top_keys:
        raise ValueError(
            f"Invalid inventory top-level keys: expected {expected_top_keys}, got {set(inventory.keys())}"
        )

    if inventory["schema_version"] != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid schema_version: expected {EXPECTED_SCHEMA_VERSION}, got {inventory['schema_version']}"
        )
    if inventory["stage"] != EXPECTED_STAGE:
        raise ValueError(
            f"Invalid stage: expected {EXPECTED_STAGE}, got {inventory['stage']}"
        )

    pred = inventory["predecessor_inventory"]
    expected_pred_keys = {"path", "sha256", "last_commit"}
    if not isinstance(pred, dict) or set(pred.keys()) != expected_pred_keys:
        raise ValueError(f"Invalid predecessor_inventory structure or keys: {pred}")

    if pred["path"] != EXPECTED_PREDECESSOR_PATH:
        raise ValueError(
            f"Invalid predecessor path: expected {EXPECTED_PREDECESSOR_PATH}, got {pred['path']}"
        )
    if pred["sha256"] != EXPECTED_PREDECESSOR_SHA:
        raise ValueError(
            f"Invalid predecessor sha256: expected {EXPECTED_PREDECESSOR_SHA}, got {pred['sha256']}"
        )
    if pred["last_commit"] != EXPECTED_PREDECESSOR_LAST_COMMIT:
        raise ValueError(
            f"Invalid predecessor last_commit: expected {EXPECTED_PREDECESSOR_LAST_COMMIT}, got {pred['last_commit']}"
        )

    actual_pred_sha = hashlib.sha256(predecessor_bytes).hexdigest()
    if actual_pred_sha != EXPECTED_PREDECESSOR_SHA or actual_pred_sha != pred["sha256"]:
        raise ValueError(
            f"Predecessor-SHA check failed: expected {EXPECTED_PREDECESSOR_SHA}, computed {actual_pred_sha}"
        )

    records = inventory["records"]
    if not isinstance(records, list) or len(records) != 1:
        raise ValueError(
            f"Expected exactly one record in records list, got {len(records) if isinstance(records, list) else type(records)}"
        )

    rec = records[0]
    expected_rec_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
    if not isinstance(rec, dict) or set(rec.keys()) != expected_rec_keys:
        raise ValueError(f"Invalid record structure or keys: {rec}")

    if rec["path"] != EXPECTED_RECORD_PATH:
        raise ValueError(
            f"Invalid record path: expected {EXPECTED_RECORD_PATH}, got {rec['path']}"
        )
    if rec["sha256"] != EXPECTED_RECORD_SHA:
        raise ValueError(
            f"Invalid record sha256: expected {EXPECTED_RECORD_SHA}, got {rec['sha256']}"
        )
    if rec["last_commit"] != EXPECTED_RECORD_LAST_COMMIT:
        raise ValueError(
            f"Invalid record last_commit: expected {EXPECTED_RECORD_LAST_COMMIT}, got {rec['last_commit']}"
        )
    if rec["seed_ids"] != EXPECTED_SEED_IDS:
        raise ValueError(
            f"Invalid seed_ids: expected {EXPECTED_SEED_IDS}, got {rec['seed_ids']}"
        )

    actual_source_sha = hashlib.sha256(source_bytes).hexdigest()
    if actual_source_sha != EXPECTED_RECORD_SHA or actual_source_sha != rec["sha256"]:
        raise ValueError(
            f"Source-SHA check failed: expected {EXPECTED_RECORD_SHA}, computed {actual_source_sha}"
        )

    selectors = rec["test_selectors"]
    if not isinstance(selectors, list) or len(selectors) != 1 or not isinstance(selectors[0], str):
        raise ValueError(
            "Selector metadata check failed: test_selectors must be a list containing exactly one string selector"
        )

    selector = selectors[0]
    parts = selector.split("::")
    if len(parts) != 3:
        raise ValueError(f"Selector metadata check failed: malformed selector '{selector}'")
    sel_path, sel_class, sel_method = parts

    if sel_path != rec["path"]:
        raise ValueError(
            f"Selector metadata check failed: selector path '{sel_path}' does not match record path '{rec['path']}'"
        )

    try:
        tree = ast.parse(source_bytes.decode("utf-8"))
    except Exception as e:
        raise ValueError(
            f"Selector metadata check failed: source bytes failed to parse as Python AST: {e}"
        ) from e

    class_node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == sel_class),
        None,
    )
    if class_node is None:
        raise ValueError(
            f"Selector metadata check failed: class '{sel_class}' not found in top-level AST"
        )

    method_node = next(
        (
            n
            for n in class_node.body
            if isinstance(n, ast.FunctionDef) and not isinstance(n, ast.AsyncFunctionDef) and n.name == sel_method
        ),
        None,
    )
    if method_node is None:
        raise ValueError(
            f"Selector metadata check failed: synchronous method '{sel_method}' not found in class '{sel_class}'"
        )

    if selector != EXPECTED_TEST_SELECTORS[0]:
        raise ValueError(
            f"Selector metadata check failed: expected exact selector '{EXPECTED_TEST_SELECTORS[0]}', got '{selector}'"
        )


class ContinuationFifteenEmptyListProvenanceTests(unittest.TestCase):
    """Hermetic unit tests for continuation 15 empty list provenance verification."""

    def setUp(self) -> None:
        self.inventory_path = INVENTORY_PATH
        self.predecessor_path = REPO_ROOT / EXPECTED_PREDECESSOR_PATH
        self.source_path = REPO_ROOT / EXPECTED_RECORD_PATH

        self.inventory_bytes = self.inventory_path.read_bytes()
        self.inventory = json.loads(self.inventory_bytes.decode("utf-8"))
        self.predecessor_bytes = self.predecessor_path.read_bytes()
        self.source_bytes = self.source_path.read_bytes()

    def test_exact_empty_list_provenance(self) -> None:
        """Verify that real inventory, predecessor, and source bytes satisfy all provenance checks."""
        validate_continuation_fifteen_provenance(
            self.inventory,
            self.predecessor_bytes,
            self.source_bytes,
        )
        self.assertEqual(
            self.inventory["records"][0]["test_selectors"],
            EXPECTED_TEST_SELECTORS,
        )

    def test_changed_source_bytes_rejected(self) -> None:
        """Verify that modifying source bytes causes failure explicitly at the source-SHA check."""
        corrupted_source_bytes = self.source_bytes + b"\n"
        with self.assertRaisesRegex(ValueError, r"Source-SHA check failed"):
            validate_continuation_fifteen_provenance(
                self.inventory,
                self.predecessor_bytes,
                corrupted_source_bytes,
            )

    def test_metadata_and_selector_drift_rejected(self) -> None:
        """Verify that metadata and selector drift cause rejection in independent subtests."""
        with self.subTest(case="predecessor_byte_drift"):
            corrupted_pred_bytes = self.predecessor_bytes + b"\n"
            with self.assertRaisesRegex(ValueError, r"Predecessor-SHA check failed"):
                validate_continuation_fifteen_provenance(
                    self.inventory,
                    corrupted_pred_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="incorrect_predecessor_commit"):
            drifted = copy.deepcopy(self.inventory)
            drifted["predecessor_inventory"]["last_commit"] = "0" * 40
            with self.assertRaisesRegex(ValueError, r"Invalid predecessor last_commit"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="incorrect_record_commit"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["last_commit"] = "0" * 40
            with self.assertRaisesRegex(ValueError, r"Invalid record last_commit"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="wrong_seed_ids"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["seed_ids"] = ["API-IDENTITY-WRONG-SEED"]
            with self.assertRaisesRegex(ValueError, r"Invalid seed_ids"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="wrong_existing_method_selector"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                f"{EXPECTED_RECORD_PATH}::StrictEmptyListAsyncSession::add"
            ]
            with self.assertRaisesRegex(ValueError, r"Selector metadata check failed: expected exact selector"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="nonexistent_method_selector"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                f"{EXPECTED_RECORD_PATH}::UsersReadAllEmptyHttpCharacterizationTests::test_nonexistent_method"
            ]
            with self.assertRaisesRegex(
                ValueError,
                r"Selector metadata check failed: synchronous method 'test_nonexistent_method' not found",
            ):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="extra_selectors"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                EXPECTED_TEST_SELECTORS[0],
                f"{EXPECTED_RECORD_PATH}::UsersReadAllEmptyHttpCharacterizationTests::test_extra",
            ]
            with self.assertRaisesRegex(
                ValueError,
                r"Selector metadata check failed: test_selectors must be a list containing exactly one string selector",
            ):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="duplicate_selectors"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                EXPECTED_TEST_SELECTORS[0],
                EXPECTED_TEST_SELECTORS[0],
            ]
            with self.assertRaisesRegex(
                ValueError,
                r"Selector metadata check failed: test_selectors must be a list containing exactly one string selector",
            ):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="extra_records"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"].append(copy.deepcopy(self.inventory["records"][0]))
            with self.assertRaisesRegex(ValueError, r"Expected exactly one record in records list"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="counter_keys"):
            drifted = copy.deepcopy(self.inventory)
            drifted["units_since_reconciliation"] = 5
            with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level keys"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="closure_keys"):
            drifted = copy.deepcopy(self.inventory)
            drifted["coverage_status"] = "COMPLETE"
            with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level keys"):
                validate_continuation_fifteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )


if __name__ == "__main__":
    unittest.main()
