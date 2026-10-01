"""Provenance characterization test for recent inventory continuation 14 execute failure.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-111
Stage: P1-00b
Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This test module indexes accepted evidence only for the users.read_all_users execute failure
characterization test (tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py),
keeps units_since_reconciliation=5 and canonical coverage 387/465 (135 baseline + 252 new,
78 remaining), and excludes ledger changes, counter edits, seed-handler-coverage changes,
predecessor edits, response-copy/HTTP/body claims, behavior acceptance, origin cells,
and High-blocker or Stage closure.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if not (REPO_ROOT / "docs").exists():
    REPO_ROOT = Path.cwd()

INVENTORY_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json"
INVENTORY_PATH = REPO_ROOT / INVENTORY_RELPATH

EXPECTED_SCHEMA_VERSION = 1
EXPECTED_STAGE = "P1-00b"

EXPECTED_PREDECESSOR_PATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"
EXPECTED_PREDECESSOR_SHA = "ebe8e3c3189f8961a61d256ed5cc690864c2e4d7f5c6d1cc91e3554060769071"
EXPECTED_PREDECESSOR_LAST_COMMIT = "e0ed45e87a45f32d19de19e2227f3af1eb969193"

EXPECTED_RECORD_PATH = "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py"
EXPECTED_RECORD_SHA = "1fd0fe0e19768903c6fd2e921b63238c2af0ceba5b6331210ce6e1e1494e0623"
EXPECTED_RECORD_LAST_COMMIT = "d175d215f83631a98a1a9fefea7c27cef6d88936"
EXPECTED_SEED_IDS = ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"]
EXPECTED_TEST_SELECTORS = [
    "tests/test_p1_00b_characterization_users_read_all_execute_failure_http.py::UsersReadAllExecuteFailureHttpCharacterizationTests::test_list_query_failure_returns_http_500_before_relation_context"
]


def validate_continuation_fourteen_provenance(
    inventory: dict,
    predecessor_bytes: bytes,
    source_bytes: bytes,
) -> None:
    """Validate exact continuation 14 schema, hashes, and AST selector resolution."""
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
    if actual_pred_sha != EXPECTED_PREDECESSOR_SHA:
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


class ContinuationFourteenExecuteFailureProvenanceTests(unittest.TestCase):
    """Hermetic unit tests for continuation 14 execute failure provenance verification."""

    def setUp(self) -> None:
        self.inventory_path = INVENTORY_PATH
        self.predecessor_path = REPO_ROOT / EXPECTED_PREDECESSOR_PATH
        self.source_path = REPO_ROOT / EXPECTED_RECORD_PATH

        self.inventory_bytes = self.inventory_path.read_bytes()
        self.inventory = json.loads(self.inventory_bytes.decode("utf-8"))
        self.predecessor_bytes = self.predecessor_path.read_bytes()
        self.source_bytes = self.source_path.read_bytes()

    def test_exact_execute_failure_provenance(self) -> None:
        """Verify that real inventory, predecessor, and source bytes satisfy all provenance checks."""
        validate_continuation_fourteen_provenance(
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
            validate_continuation_fourteen_provenance(
                self.inventory,
                self.predecessor_bytes,
                corrupted_source_bytes,
            )

    def test_selector_metadata_drift_rejected(self) -> None:
        """Verify that selector metadata drift causes failure explicitly at the selector check."""
        drifted_inventory = copy.deepcopy(self.inventory)
        drifted_inventory["records"][0]["test_selectors"] = [
            f"{EXPECTED_RECORD_PATH}::UsersReadAllExecuteFailureHttpCharacterizationTests::test_nonexistent_synchronous_method"
        ]
        with self.assertRaisesRegex(
            ValueError,
            r"Selector metadata check failed: synchronous method 'test_nonexistent_synchronous_method' not found",
        ):
            validate_continuation_fourteen_provenance(
                drifted_inventory,
                self.predecessor_bytes,
                self.source_bytes,
            )


if __name__ == "__main__":
    unittest.main()
