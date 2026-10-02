"""Provenance characterization test for recent inventory continuation 16 manager peer.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-116
Stage: P1-00b (IN_PROGRESS)
Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Gap Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work
Canonical Seed: API-IDENTITY-GET-USERS-READ-USER-L440
Accepted Source Commit: 05fe0d5c2a5dae727aa7219549843b1ce6235945
Accepted Test Selector: tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading

This module performs provenance-only validation for the users.read_user manager-peer
characterization test (tests/test_p1_00b_characterization_users_read_manager_peer_http.py)
chained from continuation-15.
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

INVENTORY_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
INVENTORY_PATH = REPO_ROOT / INVENTORY_RELPATH

EXPECTED_SCHEMA_VERSION = 1
EXPECTED_STAGE = "P1-00b"

EXPECTED_PREDECESSOR_PATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
EXPECTED_PREDECESSOR_SHA = "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
EXPECTED_PREDECESSOR_LAST_COMMIT = "8cabfffc4622d88bf006ed9ec050e0dc0b8e211c"

EXPECTED_RECORD_PATH = "tests/test_p1_00b_characterization_users_read_manager_peer_http.py"
EXPECTED_RECORD_SHA = "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd"
EXPECTED_RECORD_LAST_COMMIT = "05fe0d5c2a5dae727aa7219549843b1ce6235945"
EXPECTED_SEED_IDS = ["API-IDENTITY-GET-USERS-READ-USER-L440"]
EXPECTED_TEST_SELECTORS = [
    "tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading"
]


def validate_continuation_sixteen_provenance(
    inventory: dict,
    predecessor_bytes: bytes,
    source_bytes: bytes,
) -> None:
    """Validate exact continuation 16 schema, hashes, and AST selector resolution."""
    expected_top_keys = {"schema_version", "stage", "predecessor_inventory", "records"}
    if set(inventory.keys()) != expected_top_keys:
        raise ValueError(
            f"Invalid inventory top-level keys: expected {expected_top_keys}, got {set(inventory.keys())}"
        )

    schema_version = inventory["schema_version"]
    if type(schema_version) is not int or schema_version != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid schema_version: expected int {EXPECTED_SCHEMA_VERSION}, got {schema_version!r}"
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
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == sel_method
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

    if isinstance(method_node, ast.AsyncFunctionDef):
        raise ValueError(
            f"Selector metadata check failed: synchronous method '{sel_method}' required, but found async in class '{sel_class}'"
        )


class ContinuationSixteenManagerPeerProvenanceTests(unittest.TestCase):
    """Hermetic unit tests for continuation 16 manager peer provenance verification."""

    def setUp(self) -> None:
        self.inventory_path = INVENTORY_PATH
        self.predecessor_path = REPO_ROOT / EXPECTED_PREDECESSOR_PATH
        self.source_path = REPO_ROOT / EXPECTED_RECORD_PATH

        self.inventory_bytes = self.inventory_path.read_bytes()
        self.inventory = json.loads(self.inventory_bytes.decode("utf-8"))
        self.predecessor_bytes = self.predecessor_path.read_bytes()
        self.source_bytes = self.source_path.read_bytes()

    def test_exact_manager_peer_provenance(self) -> None:
        """Verify that real inventory, predecessor, and source bytes satisfy all provenance checks."""
        validate_continuation_sixteen_provenance(
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
            validate_continuation_sixteen_provenance(
                self.inventory,
                self.predecessor_bytes,
                corrupted_source_bytes,
            )

    def test_metadata_and_selector_drift_rejected(self) -> None:
        """Verify that metadata and selector drift cause rejection in independent subtests."""
        with self.subTest(case="predecessor_byte_drift"):
            corrupted_pred_bytes = self.predecessor_bytes + b"\n"
            with self.assertRaisesRegex(ValueError, r"Predecessor-SHA check failed"):
                validate_continuation_sixteen_provenance(
                    self.inventory,
                    corrupted_pred_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="incorrect_predecessor_commit"):
            drifted = copy.deepcopy(self.inventory)
            drifted["predecessor_inventory"]["last_commit"] = "0" * 40
            with self.assertRaisesRegex(ValueError, r"Invalid predecessor last_commit"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="incorrect_record_commit"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["last_commit"] = "0" * 40
            with self.assertRaisesRegex(ValueError, r"Invalid record last_commit"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="wrong_seed_ids"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["seed_ids"] = ["API-IDENTITY-WRONG-SEED"]
            with self.assertRaisesRegex(ValueError, r"Invalid seed_ids"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="wrong_existing_method_selector"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                f"{EXPECTED_RECORD_PATH}::FailClosedAsyncSession::get"
            ]
            with self.assertRaisesRegex(ValueError, r"Selector metadata check failed: expected exact selector"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="nonexistent_method_selector"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                f"{EXPECTED_RECORD_PATH}::UsersReadManagerPeerHttpCharacterizationTests::test_nonexistent_method"
            ]
            with self.assertRaisesRegex(
                ValueError,
                r"Selector metadata check failed: synchronous method 'test_nonexistent_method' not found",
            ):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="extra_selectors"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"][0]["test_selectors"] = [
                EXPECTED_TEST_SELECTORS[0],
                f"{EXPECTED_RECORD_PATH}::UsersReadManagerPeerHttpCharacterizationTests::test_extra",
            ]
            with self.assertRaisesRegex(
                ValueError,
                r"Selector metadata check failed: test_selectors must be a list containing exactly one string selector",
            ):
                validate_continuation_sixteen_provenance(
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
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="extra_records"):
            drifted = copy.deepcopy(self.inventory)
            drifted["records"].append(copy.deepcopy(self.inventory["records"][0]))
            with self.assertRaisesRegex(ValueError, r"Expected exactly one record in records list"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="counter_keys"):
            drifted = copy.deepcopy(self.inventory)
            drifted["units_since_reconciliation"] = 5
            with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level keys"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

        with self.subTest(case="closure_keys"):
            drifted = copy.deepcopy(self.inventory)
            drifted["coverage_status"] = "COMPLETE"
            with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level keys"):
                validate_continuation_sixteen_provenance(
                    drifted,
                    self.predecessor_bytes,
                    self.source_bytes,
                )

    def test_schema_version_wrong_types_rejected(self) -> None:
        """Verify that schema_version must be exact int 1 and rejects wrong types/values."""
        cases = [
            ("true", "true", bool),
            ("false", "false", bool),
            ("float_one", "1.0", float),
            ("float_one_point_five", "1.5", float),
            ("string_one", '"1"', str),
            ("null", "null", type(None)),
            ("empty_list", "[]", list),
            ("empty_dict", "{}", dict),
            ("integer_zero", "0", int),
            ("integer_two", "2", int),
        ]
        for case_name, raw_json, expected_type in cases:
            with self.subTest(case=case_name):
                decoded_val = json.loads(raw_json)
                self.assertIs(type(decoded_val), expected_type)
                fresh_inventory = json.loads(self.inventory_bytes.decode("utf-8"))
                fresh_inventory["schema_version"] = decoded_val
                with self.assertRaisesRegex(ValueError, r"schema_version"):
                    validate_continuation_sixteen_provenance(
                        fresh_inventory,
                        self.predecessor_bytes,
                        self.source_bytes,
                    )

        # Independently pin the canonical positive fixture exact int 1 and successfully validate it
        canonical_schema_version = json.loads("1")
        self.assertIs(type(canonical_schema_version), int)
        self.assertEqual(canonical_schema_version, 1)

        positive_inventory = json.loads(self.inventory_bytes.decode("utf-8"))
        self.assertIs(type(positive_inventory["schema_version"]), int)
        self.assertEqual(positive_inventory["schema_version"], 1)
        positive_inventory["schema_version"] = canonical_schema_version
        validate_continuation_sixteen_provenance(
            positive_inventory,
            self.predecessor_bytes,
            self.source_bytes,
        )


if __name__ == "__main__":
    unittest.main()
