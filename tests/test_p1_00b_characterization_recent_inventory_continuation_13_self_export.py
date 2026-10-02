"""Provenance characterization test for recent inventory continuation 13.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-111
Stage: P1-00b
Blocker ID: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This test module indexes accepted evidence only for the self-export delegation
characterization test (tests/test_p1_00b_characterization_trade_export_self_delegation.py),
keeps all five units pending and canonical coverage 387/465 (135 baseline + 252 new,
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

INVENTORY_RELPATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"
INVENTORY_PATH = REPO_ROOT / INVENTORY_RELPATH

EXPECTED_SCHEMA_VERSION = 1
EXPECTED_STAGE = "P1-00b"

EXPECTED_PREDECESSOR_PATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json"
EXPECTED_PREDECESSOR_SHA = "75fd2ee23c469393d7ad197ed697671179a3670a145ddbd7f7ea62e247fb8392"
EXPECTED_PREDECESSOR_LAST_COMMIT = "3e8fd101e41697b6ed8cc53c8f19f11fb3cf591c"

EXPECTED_RECORD_PATH = "tests/test_p1_00b_characterization_trade_export_self_delegation.py"
EXPECTED_RECORD_SHA = "b8db918415014c2fbaec56888bdc37e6be53f329829a6a1af9dd0ba72b5856d1"
EXPECTED_RECORD_LAST_COMMIT = "98c98e93e94aa426ebae7502a67a5d5053a3a31a"
EXPECTED_SEED_IDS = ["API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797"]
EXPECTED_TEST_SELECTORS = [
    "tests/test_p1_00b_characterization_trade_export_self_delegation.py::TradeExportSelfDelegationCharacterizationTests::test_self_target_delegates_exact_filters_and_returns_export_response"
]


class _StrSubclass(str):
    """String subclass for exact type validation checks."""


class _DictSubclass(dict):
    """Dict subclass for exact type validation checks."""


class _IntSubclass(int):
    """Int subclass for exact type validation checks."""


class _ListSubclass(list):
    """List subclass for exact type validation checks."""


def validate_continuation_thirteen_provenance(
    inventory: dict,
    predecessor_bytes: bytes,
    source_bytes: bytes,
) -> None:
    """Validate exact continuation 13 schema, hashes, and AST selector resolution."""
    if type(inventory) is not dict:
        raise ValueError(
            f"Invalid inventory container: expected dict, got {type(inventory)}"
        )

    if not all(type(k) is str for k in inventory.keys()):
        raise ValueError(
            "Invalid inventory top-level key type: all keys must be exact builtin str"
        )

    expected_top_keys = {"schema_version", "stage", "predecessor_inventory", "records"}
    if set(inventory.keys()) != expected_top_keys:
        raise ValueError(
            f"Invalid inventory top-level keys: expected {expected_top_keys}, got {set(inventory.keys())}"
        )

    if type(inventory["schema_version"]) is not int:
        raise ValueError(
            f"Invalid schema_version type: expected int, got {type(inventory['schema_version'])}"
        )
    if inventory["schema_version"] != EXPECTED_SCHEMA_VERSION:
        raise ValueError(
            f"Invalid schema_version: expected {EXPECTED_SCHEMA_VERSION}, got {inventory['schema_version']}"
        )

    if type(inventory["stage"]) is not str:
        raise ValueError(
            f"Invalid stage type: expected str, got {type(inventory['stage'])}"
        )
    if inventory["stage"] != EXPECTED_STAGE:
        raise ValueError(
            f"Invalid stage: expected {EXPECTED_STAGE}, got {inventory['stage']}"
        )

    pred = inventory["predecessor_inventory"]
    if type(pred) is not dict:
        raise ValueError(
            f"Invalid predecessor_inventory structure: expected dict, got {type(pred)}"
        )
    if not all(type(k) is str for k in pred.keys()):
        raise ValueError(
            "Invalid predecessor_inventory key type: all keys must be exact builtin str"
        )
    expected_pred_keys = {"path", "sha256", "last_commit"}
    if set(pred.keys()) != expected_pred_keys:
        raise ValueError(f"Invalid predecessor_inventory structure or keys: {pred}")

    if type(pred["path"]) is not str:
        raise ValueError(
            f"Invalid predecessor path type: expected str, got {type(pred['path'])}"
        )
    if pred["path"] != EXPECTED_PREDECESSOR_PATH:
        raise ValueError(
            f"Invalid predecessor path: expected {EXPECTED_PREDECESSOR_PATH}, got {pred['path']}"
        )

    if type(pred["sha256"]) is not str:
        raise ValueError(
            f"Invalid predecessor sha256 type: expected str, got {type(pred['sha256'])}"
        )
    if pred["sha256"] != EXPECTED_PREDECESSOR_SHA:
        raise ValueError(
            f"Invalid predecessor sha256: expected {EXPECTED_PREDECESSOR_SHA}, got {pred['sha256']}"
        )

    if type(pred["last_commit"]) is not str:
        raise ValueError(
            f"Invalid predecessor last_commit type: expected str, got {type(pred['last_commit'])}"
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
    if type(records) is not list or len(records) != 1:
        raise ValueError(
            f"Expected exactly one record in records list, got {len(records) if type(records) is list else type(records)}"
        )

    rec = records[0]
    if type(rec) is not dict:
        raise ValueError(f"Invalid record structure: expected dict, got {type(rec)}")
    if not all(type(k) is str for k in rec.keys()):
        raise ValueError(
            "Invalid record key type: all keys must be exact builtin str"
        )
    expected_rec_keys = {"path", "sha256", "last_commit", "seed_ids", "test_selectors"}
    if set(rec.keys()) != expected_rec_keys:
        raise ValueError(f"Invalid record structure or keys: {rec}")

    if type(rec["path"]) is not str:
        raise ValueError(
            f"Invalid record path type: expected str, got {type(rec['path'])}"
        )
    if rec["path"] != EXPECTED_RECORD_PATH:
        raise ValueError(
            f"Invalid record path: expected {EXPECTED_RECORD_PATH}, got {rec['path']}"
        )

    if type(rec["sha256"]) is not str:
        raise ValueError(
            f"Invalid record sha256 type: expected str, got {type(rec['sha256'])}"
        )
    if rec["sha256"] != EXPECTED_RECORD_SHA:
        raise ValueError(
            f"Invalid record sha256: expected {EXPECTED_RECORD_SHA}, got {rec['sha256']}"
        )

    if type(rec["last_commit"]) is not str:
        raise ValueError(
            f"Invalid record last_commit type: expected str, got {type(rec['last_commit'])}"
        )
    if rec["last_commit"] != EXPECTED_RECORD_LAST_COMMIT:
        raise ValueError(
            f"Invalid record last_commit: expected {EXPECTED_RECORD_LAST_COMMIT}, got {rec['last_commit']}"
        )

    if (
        type(rec["seed_ids"]) is not list
        or len(rec["seed_ids"]) != 1
        or type(rec["seed_ids"][0]) is not str
    ):
        raise ValueError(
            f"Invalid seed_ids: expected list containing exactly one str, got {rec.get('seed_ids') if type(rec) is dict else rec}"
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
    if (
        type(selectors) is not list
        or len(selectors) != 1
        or type(selectors[0]) is not str
    ):
        raise ValueError(
            "Selector metadata check failed: test_selectors must be a list containing exactly one string selector"
        )
    if selectors != EXPECTED_TEST_SELECTORS:
        raise ValueError(
            f"Invalid test_selectors: expected {EXPECTED_TEST_SELECTORS}, got {selectors}"
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
            if isinstance(n, ast.AsyncFunctionDef) and n.name == sel_method
        ),
        None,
    )
    if method_node is None:
        raise ValueError(
            f"Selector metadata check failed: async method '{sel_method}' not found in class '{sel_class}'"
        )


class ContinuationThirteenSelfExportProvenanceTests(unittest.TestCase):
    """Hermetic unit tests for continuation 13 self-export provenance verification."""

    def setUp(self) -> None:
        self.inventory_path = INVENTORY_PATH
        self.predecessor_path = REPO_ROOT / EXPECTED_PREDECESSOR_PATH
        self.source_path = REPO_ROOT / EXPECTED_RECORD_PATH

        self.inventory_bytes = self.inventory_path.read_bytes()
        self.inventory = json.loads(self.inventory_bytes.decode("utf-8"))
        self.predecessor_bytes = self.predecessor_path.read_bytes()
        self.source_bytes = self.source_path.read_bytes()

    def test_exact_self_export_provenance(self) -> None:
        """Verify that real inventory, predecessor, and source bytes satisfy all provenance checks."""
        validate_continuation_thirteen_provenance(
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
            validate_continuation_thirteen_provenance(
                self.inventory,
                self.predecessor_bytes,
                corrupted_source_bytes,
            )

    def test_selector_metadata_drift_rejected(self) -> None:
        """Verify that selector metadata drift causes failure explicitly at the selector check."""
        drifted_inventory = copy.deepcopy(self.inventory)
        drifted_inventory["records"][0]["test_selectors"] = [
            f"{EXPECTED_RECORD_PATH}::TradeExportSelfDelegationCharacterizationTests::test_nonexistent_method_drift"
        ]
        with self.assertRaisesRegex(
            ValueError,
            r"Invalid test_selectors",
        ):
            validate_continuation_thirteen_provenance(
                drifted_inventory,
                self.predecessor_bytes,
                self.source_bytes,
            )

    def test_reproduced_defects_rejected(self) -> None:
        """Verify explicit rejection of the four reproduced baseline validator defects."""
        # 1. schema_version = True (bool subclass of int)
        mut_bool_ver = copy.deepcopy(self.inventory)
        mut_bool_ver["schema_version"] = True
        with self.assertRaisesRegex(ValueError, r"Invalid schema_version type"):
            validate_continuation_thirteen_provenance(
                mut_bool_ver, self.predecessor_bytes, self.source_bytes
            )

        # 2. str-subclass top-level key
        mut_str_key = {_StrSubclass(k): v for k, v in self.inventory.items()}
        with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level key type"):
            validate_continuation_thirteen_provenance(
                mut_str_key, self.predecessor_bytes, self.source_bytes
            )

        # 3. dict-subclass predecessor
        mut_dict_pred = copy.deepcopy(self.inventory)
        mut_dict_pred["predecessor_inventory"] = _DictSubclass(
            mut_dict_pred["predecessor_inventory"]
        )
        with self.assertRaisesRegex(
            ValueError, r"Invalid predecessor_inventory structure"
        ):
            validate_continuation_thirteen_provenance(
                mut_dict_pred, self.predecessor_bytes, self.source_bytes
            )

        # 4. str-subclass record path
        mut_str_path = copy.deepcopy(self.inventory)
        mut_str_path["records"][0]["path"] = _StrSubclass(
            mut_str_path["records"][0]["path"]
        )
        with self.assertRaisesRegex(ValueError, r"Invalid record path type"):
            validate_continuation_thirteen_provenance(
                mut_str_path, self.predecessor_bytes, self.source_bytes
            )

    def test_subclasses_and_strict_container_types_rejected(self) -> None:
        """Verify that subclasses of int, list, dict, and str are strictly rejected."""
        # schema_version int subclass
        mut_int_sub = copy.deepcopy(self.inventory)
        mut_int_sub["schema_version"] = _IntSubclass(1)
        with self.assertRaisesRegex(ValueError, r"Invalid schema_version type"):
            validate_continuation_thirteen_provenance(
                mut_int_sub, self.predecessor_bytes, self.source_bytes
            )

        # records list subclass
        mut_list_sub = copy.deepcopy(self.inventory)
        mut_list_sub["records"] = _ListSubclass(mut_list_sub["records"])
        with self.assertRaisesRegex(
            ValueError, r"Expected exactly one record in records list"
        ):
            validate_continuation_thirteen_provenance(
                mut_list_sub, self.predecessor_bytes, self.source_bytes
            )

        # record dict subclass
        mut_rec_sub = copy.deepcopy(self.inventory)
        mut_rec_sub["records"][0] = _DictSubclass(mut_rec_sub["records"][0])
        with self.assertRaisesRegex(ValueError, r"Invalid record structure"):
            validate_continuation_thirteen_provenance(
                mut_rec_sub, self.predecessor_bytes, self.source_bytes
            )

        # predecessor key str subclass
        mut_pred_key_sub = copy.deepcopy(self.inventory)
        mut_pred_key_sub["predecessor_inventory"] = {
            _StrSubclass(k): v
            for k, v in mut_pred_key_sub["predecessor_inventory"].items()
        }
        with self.assertRaisesRegex(
            ValueError, r"Invalid predecessor_inventory key type"
        ):
            validate_continuation_thirteen_provenance(
                mut_pred_key_sub, self.predecessor_bytes, self.source_bytes
            )

        # record key str subclass
        mut_rec_key_sub = copy.deepcopy(self.inventory)
        mut_rec_key_sub["records"][0] = {
            _StrSubclass(k): v
            for k, v in mut_rec_key_sub["records"][0].items()
        }
        with self.assertRaisesRegex(ValueError, r"Invalid record key type"):
            validate_continuation_thirteen_provenance(
                mut_rec_key_sub, self.predecessor_bytes, self.source_bytes
            )

    def test_nested_container_and_leaf_types_rejected(self) -> None:
        """Verify that non-builtin types at container and leaf locations raise specific ValueErrors."""
        # top container not dict
        with self.assertRaisesRegex(ValueError, r"Invalid inventory container"):
            validate_continuation_thirteen_provenance(
                [], self.predecessor_bytes, self.source_bytes  # type: ignore[arg-type]
            )

        # type check cases: (mutation_lambda, expected_regex)
        cases = [
            (lambda inv: inv.update({"schema_version": "1"}), r"Invalid schema_version type"),
            (lambda inv: inv.update({"stage": 123}), r"Invalid stage type"),
            (lambda inv: inv.update({"predecessor_inventory": "not_dict"}), r"Invalid predecessor_inventory structure"),
            (lambda inv: inv["predecessor_inventory"].update({"path": 123}), r"Invalid predecessor path type"),
            (lambda inv: inv["predecessor_inventory"].update({"sha256": 123}), r"Invalid predecessor sha256 type"),
            (lambda inv: inv["predecessor_inventory"].update({"last_commit": 123}), r"Invalid predecessor last_commit type"),
            (lambda inv: inv.update({"records": {}}), r"Expected exactly one record in records list"),
            (lambda inv: inv.update({"records": ["not_dict"]}), r"Invalid record structure"),
            (lambda inv: inv["records"][0].update({"path": 123}), r"Invalid record path type"),
            (lambda inv: inv["records"][0].update({"sha256": 123}), r"Invalid record sha256 type"),
            (lambda inv: inv["records"][0].update({"last_commit": 123}), r"Invalid record last_commit type"),
            (lambda inv: inv["records"][0].update({"seed_ids": "not_list"}), r"Invalid seed_ids"),
            (lambda inv: inv["records"][0].update({"seed_ids": [123]}), r"Invalid seed_ids"),
            (lambda inv: inv["records"][0].update({"test_selectors": "not_list"}), r"Selector metadata check failed: test_selectors must be a list"),
            (lambda inv: inv["records"][0].update({"test_selectors": [123]}), r"Selector metadata check failed: test_selectors must be a list"),
        ]
        for mutate_fn, expected_regex in cases:
            with self.subTest(expected_regex=expected_regex):
                mut = copy.deepcopy(self.inventory)
                mutate_fn(mut)
                with self.assertRaisesRegex(ValueError, expected_regex):
                    validate_continuation_thirteen_provenance(
                        mut, self.predecessor_bytes, self.source_bytes
                    )

    def test_missing_and_extra_keys_rejected(self) -> None:
        """Verify that missing and extra keys at all schema levels raise specific ValueErrors."""
        # Top-level missing
        for key in ["schema_version", "stage", "predecessor_inventory", "records"]:
            with self.subTest(top_missing=key):
                mut = copy.deepcopy(self.inventory)
                del mut[key]
                with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level keys"):
                    validate_continuation_thirteen_provenance(
                        mut, self.predecessor_bytes, self.source_bytes
                    )

        # Top-level extra
        mut_top_extra = copy.deepcopy(self.inventory)
        mut_top_extra["unexpected_extra_key"] = "extra_value"
        with self.assertRaisesRegex(ValueError, r"Invalid inventory top-level keys"):
            validate_continuation_thirteen_provenance(
                mut_top_extra, self.predecessor_bytes, self.source_bytes
            )

        # Predecessor missing
        for key in ["path", "sha256", "last_commit"]:
            with self.subTest(pred_missing=key):
                mut = copy.deepcopy(self.inventory)
                del mut["predecessor_inventory"][key]
                with self.assertRaisesRegex(
                    ValueError, r"Invalid predecessor_inventory structure or keys"
                ):
                    validate_continuation_thirteen_provenance(
                        mut, self.predecessor_bytes, self.source_bytes
                    )

        # Predecessor extra
        mut_pred_extra = copy.deepcopy(self.inventory)
        mut_pred_extra["predecessor_inventory"]["unexpected_extra_key"] = "extra"
        with self.assertRaisesRegex(
            ValueError, r"Invalid predecessor_inventory structure or keys"
        ):
            validate_continuation_thirteen_provenance(
                mut_pred_extra, self.predecessor_bytes, self.source_bytes
            )

        # Record missing
        for key in ["path", "sha256", "last_commit", "seed_ids", "test_selectors"]:
            with self.subTest(record_missing=key):
                mut = copy.deepcopy(self.inventory)
                del mut["records"][0][key]
                with self.assertRaisesRegex(ValueError, r"Invalid record structure or keys"):
                    validate_continuation_thirteen_provenance(
                        mut, self.predecessor_bytes, self.source_bytes
                    )

        # Record extra
        mut_rec_extra = copy.deepcopy(self.inventory)
        mut_rec_extra["records"][0]["unexpected_extra_key"] = "extra"
        with self.assertRaisesRegex(ValueError, r"Invalid record structure or keys"):
            validate_continuation_thirteen_provenance(
                mut_rec_extra, self.predecessor_bytes, self.source_bytes
            )

    def test_list_cardinalities_rejected(self) -> None:
        """Verify that cardinality violations in records, seed_ids, and test_selectors raise ValueError."""
        # records empty
        mut_rec_empty = copy.deepcopy(self.inventory)
        mut_rec_empty["records"] = []
        with self.assertRaisesRegex(
            ValueError, r"Expected exactly one record in records list"
        ):
            validate_continuation_thirteen_provenance(
                mut_rec_empty, self.predecessor_bytes, self.source_bytes
            )

        # records > 1
        mut_rec_two = copy.deepcopy(self.inventory)
        mut_rec_two["records"].append(copy.deepcopy(mut_rec_two["records"][0]))
        with self.assertRaisesRegex(
            ValueError, r"Expected exactly one record in records list"
        ):
            validate_continuation_thirteen_provenance(
                mut_rec_two, self.predecessor_bytes, self.source_bytes
            )

        # seed_ids empty
        mut_seeds_empty = copy.deepcopy(self.inventory)
        mut_seeds_empty["records"][0]["seed_ids"] = []
        with self.assertRaisesRegex(ValueError, r"Invalid seed_ids"):
            validate_continuation_thirteen_provenance(
                mut_seeds_empty, self.predecessor_bytes, self.source_bytes
            )

        # seed_ids > 1
        mut_seeds_two = copy.deepcopy(self.inventory)
        mut_seeds_two["records"][0]["seed_ids"] = [
            EXPECTED_SEED_IDS[0],
            "API-EXTRA-SEED-ID-L9999",
        ]
        with self.assertRaisesRegex(ValueError, r"Invalid seed_ids"):
            validate_continuation_thirteen_provenance(
                mut_seeds_two, self.predecessor_bytes, self.source_bytes
            )

        # test_selectors empty
        mut_sel_empty = copy.deepcopy(self.inventory)
        mut_sel_empty["records"][0]["test_selectors"] = []
        with self.assertRaisesRegex(
            ValueError,
            r"Selector metadata check failed: test_selectors must be a list containing exactly one string selector",
        ):
            validate_continuation_thirteen_provenance(
                mut_sel_empty, self.predecessor_bytes, self.source_bytes
            )

        # test_selectors > 1
        mut_sel_two = copy.deepcopy(self.inventory)
        mut_sel_two["records"][0]["test_selectors"] = [
            EXPECTED_TEST_SELECTORS[0],
            EXPECTED_TEST_SELECTORS[0],
        ]
        with self.assertRaisesRegex(
            ValueError,
            r"Selector metadata check failed: test_selectors must be a list containing exactly one string selector",
        ):
            validate_continuation_thirteen_provenance(
                mut_sel_two, self.predecessor_bytes, self.source_bytes
            )

    def test_independently_wrong_pinned_identities_rejected(self) -> None:
        """Verify that mutations to each pinned field and byte SHA are independently rejected."""
        # schema_version wrong int
        mut_ver = copy.deepcopy(self.inventory)
        mut_ver["schema_version"] = 2
        with self.assertRaisesRegex(ValueError, r"Invalid schema_version: expected 1"):
            validate_continuation_thirteen_provenance(
                mut_ver, self.predecessor_bytes, self.source_bytes
            )

        # stage wrong str
        mut_stage = copy.deepcopy(self.inventory)
        mut_stage["stage"] = "P1-00"
        with self.assertRaisesRegex(ValueError, r"Invalid stage: expected P1-00b"):
            validate_continuation_thirteen_provenance(
                mut_stage, self.predecessor_bytes, self.source_bytes
            )

        # predecessor path
        mut_pred_path = copy.deepcopy(self.inventory)
        mut_pred_path["predecessor_inventory"]["path"] = "docs/refactor/two-server/section-1/inventory/wrong.json"
        with self.assertRaisesRegex(ValueError, r"Invalid predecessor path"):
            validate_continuation_thirteen_provenance(
                mut_pred_path, self.predecessor_bytes, self.source_bytes
            )

        # predecessor sha256
        mut_pred_sha = copy.deepcopy(self.inventory)
        mut_pred_sha["predecessor_inventory"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, r"Invalid predecessor sha256"):
            validate_continuation_thirteen_provenance(
                mut_pred_sha, self.predecessor_bytes, self.source_bytes
            )

        # predecessor last_commit
        mut_pred_commit = copy.deepcopy(self.inventory)
        mut_pred_commit["predecessor_inventory"]["last_commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, r"Invalid predecessor last_commit"):
            validate_continuation_thirteen_provenance(
                mut_pred_commit, self.predecessor_bytes, self.source_bytes
            )

        # predecessor bytes hash mismatch
        corrupted_pred_bytes = self.predecessor_bytes + b"\n"
        with self.assertRaisesRegex(ValueError, r"Predecessor-SHA check failed"):
            validate_continuation_thirteen_provenance(
                self.inventory, corrupted_pred_bytes, self.source_bytes
            )

        # record path
        mut_rec_path = copy.deepcopy(self.inventory)
        mut_rec_path["records"][0]["path"] = "tests/test_wrong_path.py"
        with self.assertRaisesRegex(ValueError, r"Invalid record path"):
            validate_continuation_thirteen_provenance(
                mut_rec_path, self.predecessor_bytes, self.source_bytes
            )

        # record sha256
        mut_rec_sha = copy.deepcopy(self.inventory)
        mut_rec_sha["records"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, r"Invalid record sha256"):
            validate_continuation_thirteen_provenance(
                mut_rec_sha, self.predecessor_bytes, self.source_bytes
            )

        # record last_commit
        mut_rec_commit = copy.deepcopy(self.inventory)
        mut_rec_commit["records"][0]["last_commit"] = "0" * 40
        with self.assertRaisesRegex(ValueError, r"Invalid record last_commit"):
            validate_continuation_thirteen_provenance(
                mut_rec_commit, self.predecessor_bytes, self.source_bytes
            )

        # record seed_ids identity
        mut_rec_seeds = copy.deepcopy(self.inventory)
        mut_rec_seeds["records"][0]["seed_ids"] = ["API-DIFFERENT-SEED-ID-L1234"]
        with self.assertRaisesRegex(ValueError, r"Invalid seed_ids"):
            validate_continuation_thirteen_provenance(
                mut_rec_seeds, self.predecessor_bytes, self.source_bytes
            )

        # record test_selectors identity
        mut_rec_sel = copy.deepcopy(self.inventory)
        mut_rec_sel["records"][0]["test_selectors"] = [
            f"{EXPECTED_RECORD_PATH}::TradeExportSelfDelegationCharacterizationTests::test_different_selector"
        ]
        with self.assertRaisesRegex(ValueError, r"Invalid test_selectors"):
            validate_continuation_thirteen_provenance(
                mut_rec_sel, self.predecessor_bytes, self.source_bytes
            )

    def test_canonical_pass_after_negative_cases(self) -> None:
        """Demonstrate that canonical inventory and original bytes pass cleanly after negative test runs."""
        validate_continuation_thirteen_provenance(
            self.inventory,
            self.predecessor_bytes,
            self.source_bytes,
        )
        self.assertEqual(
            self.inventory["records"][0]["test_selectors"],
            EXPECTED_TEST_SELECTORS,
        )


if __name__ == "__main__":
    unittest.main()
