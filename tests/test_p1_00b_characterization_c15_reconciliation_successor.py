from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import unittest

from tests.test_p1_00b_characterization_c14_c15_record_ownership import (
    FROZEN_C14_INVENTORY,
    FROZEN_C15_INVENTORY,
)
from tests.test_p1_00b_characterization_c17_partial_reconciliation import (
    REPO_ROOT,
    assert_reconciliation_seventeen_oracle,
)
from tests.test_p1_00b_characterization_inventory_evidence_helper import (
    assert_record_evidence,
)
from tests.test_p1_00b_characterization_recent_inventory_continuation_15_empty_list import (
    validate_continuation_fifteen_provenance,
)

EXPECTED_PRIOR_RECEIPT_PATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json"
EXPECTED_PRIOR_RECEIPT_SHA = "66c4ff5427b2e831dc386c663151113e52e1c40fda845b741a1eae84cf5f1295"
EXPECTED_PRIOR_RECEIPT_LAST_COMMIT = "6087add7b3d706456ad866ed686eafb12d233c12"

EXPECTED_C15_INVENTORY_PATH = "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
EXPECTED_C15_INVENTORY_SHA = "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
EXPECTED_C15_INVENTORY_LAST_COMMIT = "8cabfffc4622d88bf006ed9ec050e0dc0b8e211c"

EXPECTED_C15_RECORD_PATH = "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
EXPECTED_C15_RECORD_SHA = "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
EXPECTED_C15_RECORD_LAST_COMMIT = "f564bf674a6c5f7ab38be28a2ad92f69ff886ab8"

FROZEN_EXPECTED_RECONCILIATION_C15_FROM_C17: dict[str, object] = {
    "schema_version": 1,
    "stage": "P1-00b",
    "assignment_id": "WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169",
    "inspected_head": "117cc9ea12197f767c41b7f3d4db6ab1ab728570",
    "status": "PARTIAL_RECONCILIATION",
    "historical_checkpoint": {
        "last_commit": EXPECTED_PRIOR_RECEIPT_LAST_COMMIT,
        "path": EXPECTED_PRIOR_RECEIPT_PATH,
        "sha256": EXPECTED_PRIOR_RECEIPT_SHA,
    },
    "source_inventory": {
        "last_commit": EXPECTED_C15_INVENTORY_LAST_COMMIT,
        "path": EXPECTED_C15_INVENTORY_PATH,
        "sha256": EXPECTED_C15_INVENTORY_SHA,
    },
    "records": [
        {
            "last_commit": EXPECTED_C15_RECORD_LAST_COMMIT,
            "path": EXPECTED_C15_RECORD_PATH,
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407",
            ],
            "sha256": EXPECTED_C15_RECORD_SHA,
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_all_empty_http.py::UsersReadAllEmptyHttpCharacterizationTests::test_empty_list_returns_http_200_without_relation_queries",
            ],
        },
    ],
    "delta_distinct_seed_count": 0,
    "pre_reconciled_delta_seed_ids": [
        "API-IDENTITY-GET-USERS-READ-ALL-USERS-L407",
    ],
    "units_since_reconciliation": 5,
    "total_behavior_units": 5,
    "pending_five": 5,
    "delta_reconciled_behavior_units": 1,
    "historical_prior_reconciled_units": 3,
    "reconciled_behavior_units": 4,
    "remaining_behavior_units": 1,
    "cumulative_inventory_paths": [
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json",
    ],
    "remaining_inventory_paths": [
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
    ],
    "remaining_behavior_descriptions": {
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json": "manager-peer HTTP403",
    },
    "reconciliation_policy": {
        "no_sixth_behavior_accepted_before_complete_reconciliation": True,
        "deferred_continuations": [
            "continuation-12",
        ],
        "deferred_proposals": [
            "153/154/155/157-C001",
            "normal159 C1/C2",
            "rejected C15 machine checkpoint",
            "rejected policy/seed proposals",
            "normal161-c5 C16 incremental reconciliation",
            "subsequent recorded deferrals",
        ],
        "global_reconciliation_complete": False,
    },
    "counts": {
        "baseline_evidenced_seed_count": 135,
        "reconciled_new_seed_count": 252,
        "evidenced_seed_count": 387,
        "remaining_seed_count": 78,
        "expected_seed_count": 465,
    },
    "coverage_status": "PARTIAL",
    "seed_acceptance_status": "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
    "seed_accounting": {
        "prospective_distinct_seed_count": 1,
        "prospective_distinct_seeds": [
            "API-IDENTITY-GET-USERS-READ-USER-L440",
        ],
        "overlapping_pending_seeds": [
            {
                "continuation_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
                "note": "overlapping C16 must never increment prospective distinct seed count again",
                "seed_id": "API-IDENTITY-GET-USERS-READ-USER-L440",
            },
        ],
    },
    "canonical_blockers": [
        "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
        "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
    ],
    "stages": {
        "P1-00": "COMPLETE",
        "P1-00b": "IN_PROGRESS",
    },
    "origin_matrix_unresolved_cells": 24,
    "closures_authorized": False,
    "exclusions": [
        "read_user self-success",
        "super-admin rejection",
        "authorization policy coverage",
        "mutation",
        "persistence",
        "retry",
        "reload",
        "origin matrix cells",
        "seed-blocker closure",
        "Stage closure",
    ],
}


def _assert_strict_json_type_equal(actual: object, expected: object, path: str = "root") -> None:
    if type(actual) is not type(expected):
        raise AssertionError(
            f"Type mismatch at {path}: expected {type(expected).__name__}, got {type(actual).__name__}"
        )
    if isinstance(actual, dict):
        if set(actual.keys()) != set(expected.keys()):
            raise AssertionError(
                f"Key set mismatch at {path}: expected {set(expected.keys())}, got {set(actual.keys())}"
            )
        for k, exp_val in expected.items():
            _assert_strict_json_type_equal(actual[k], exp_val, f"{path}.{k}")
    elif isinstance(actual, list):
        if len(actual) != len(expected):
            raise AssertionError(
                f"List length mismatch at {path}: expected {len(expected)}, got {len(actual)}"
            )
        for idx, (act_item, exp_item) in enumerate(zip(actual, expected)):
            _assert_strict_json_type_equal(act_item, exp_item, f"{path}[{idx}]")
    else:
        if actual != expected:
            raise AssertionError(
                f"Value mismatch at {path}: expected {expected!r}, got {actual!r}"
            )


def assert_reconciliation_c15_from_c17_oracle(
    data: dict[str, object],
    evidence_bytes: dict[str, bytes] | None = None,
    test_case: unittest.TestCase | None = None,
) -> None:
    if type(data) is not dict:
        raise AssertionError(f"Top-level receipt must be a dict, got {type(data).__name__}")

    tc = test_case if test_case is not None else unittest.TestCase()

    if "schema_version" not in data:
        raise AssertionError("Field 'schema_version' missing")
    sv = data["schema_version"]
    if type(sv) is not int:
        raise AssertionError(
            f"Field 'schema_version' must be exact int, got {type(sv).__name__} ({sv!r})"
        )
    if sv != 1:
        raise AssertionError(f"Field 'schema_version' must be 1, got {sv!r}")

    root_counters = [
        ("units_since_reconciliation", 5),
        ("total_behavior_units", 5),
        ("pending_five", 5),
        ("delta_reconciled_behavior_units", 1),
        ("historical_prior_reconciled_units", 3),
        ("reconciled_behavior_units", 4),
        ("remaining_behavior_units", 1),
        ("origin_matrix_unresolved_cells", 24),
        ("delta_distinct_seed_count", 0),
    ]
    for key, expected_val in root_counters:
        if key not in data:
            raise AssertionError(f"Root counter field {key!r} missing")
        val = data[key]
        if type(val) is not int:
            raise AssertionError(
                f"Field {key!r} must be exact int, got {type(val).__name__} ({val!r})"
            )
        if val != expected_val:
            raise AssertionError(f"Field {key!r} must be {expected_val}, got {val!r}")

    counts = data.get("counts")
    if type(counts) is not dict:
        raise AssertionError(f"Field 'counts' must be dict, got {type(counts).__name__}")
    nested_counters = [
        ("counts.baseline_evidenced_seed_count", "baseline_evidenced_seed_count", 135),
        ("counts.reconciled_new_seed_count", "reconciled_new_seed_count", 252),
        ("counts.evidenced_seed_count", "evidenced_seed_count", 387),
        ("counts.remaining_seed_count", "remaining_seed_count", 78),
        ("counts.expected_seed_count", "expected_seed_count", 465),
    ]
    for full_path, key, expected_val in nested_counters:
        if key not in counts:
            raise AssertionError(f"Field {full_path!r} missing")
        val = counts[key]
        if type(val) is not int:
            raise AssertionError(
                f"Field {full_path!r} must be exact int, got {type(val).__name__} ({val!r})"
            )
        if val != expected_val:
            raise AssertionError(f"Field {full_path!r} must be {expected_val}, got {val!r}")

    seed_acc = data.get("seed_accounting")
    if type(seed_acc) is not dict:
        raise AssertionError(f"Field 'seed_accounting' must be dict, got {type(seed_acc).__name__}")
    if "prospective_distinct_seed_count" not in seed_acc:
        raise AssertionError("Field 'seed_accounting.prospective_distinct_seed_count' missing")
    pdsc = seed_acc["prospective_distinct_seed_count"]
    if type(pdsc) is not int:
        raise AssertionError(
            f"Field 'seed_accounting.prospective_distinct_seed_count' must be exact int, got {type(pdsc).__name__} ({pdsc!r})"
        )
    if pdsc != 1:
        raise AssertionError(
            f"Field 'seed_accounting.prospective_distinct_seed_count' must be 1, got {pdsc!r}"
        )

    _assert_strict_json_type_equal(data, FROZEN_EXPECTED_RECONCILIATION_C15_FROM_C17)

    if evidence_bytes is None:
        evidence_bytes = {
            "historical_checkpoint": (REPO_ROOT / str(data["historical_checkpoint"]["path"])).read_bytes(),
            "source_inventory": (REPO_ROOT / str(data["source_inventory"]["path"])).read_bytes(),
            "predecessor_inventory": (REPO_ROOT / "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json").read_bytes(),
            "record": (REPO_ROOT / str(data["records"][0]["path"])).read_bytes(),
        }

    cp_sha = hashlib.sha256(evidence_bytes["historical_checkpoint"]).hexdigest()
    if cp_sha != EXPECTED_PRIOR_RECEIPT_SHA or cp_sha != data["historical_checkpoint"]["sha256"]:
        raise AssertionError(
            f"Historical checkpoint SHA check failed: expected {EXPECTED_PRIOR_RECEIPT_SHA}, got {cp_sha}"
        )

    src_sha = hashlib.sha256(evidence_bytes["source_inventory"]).hexdigest()
    if src_sha != EXPECTED_C15_INVENTORY_SHA or src_sha != data["source_inventory"]["sha256"]:
        raise AssertionError(
            f"Source inventory SHA check failed: expected {EXPECTED_C15_INVENTORY_SHA}, got {src_sha}"
        )

    c17_dict = json.loads(evidence_bytes["historical_checkpoint"].decode("utf-8"))
    assert_reconciliation_seventeen_oracle(c17_dict)

    c15_dict = json.loads(evidence_bytes["source_inventory"].decode("utf-8"))
    try:
        validate_continuation_fifteen_provenance(
            c15_dict,
            evidence_bytes["predecessor_inventory"],
            evidence_bytes["record"],
        )
    except ValueError as exc:
        raise AssertionError(f"validate_continuation_fifteen_provenance failed: {exc}") from exc

    if data["records"][0] != c15_dict["records"][0]:
        raise AssertionError(
            f"Record in receipt does not match record in C15 inventory: {data['records'][0]} != {c15_dict['records'][0]}"
        )
    assert_record_evidence(tc, data["records"][0], REPO_ROOT)


class C15ReconciliationSuccessorTests(unittest.TestCase):
    def test_exact_c15_increment(self) -> None:
        receipt_path = (
            REPO_ROOT
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json"
        )
        receipt_bytes = receipt_path.read_bytes()
        actual_receipt = json.loads(receipt_bytes.decode("utf-8"))

        assert_reconciliation_c15_from_c17_oracle(actual_receipt, test_case=self)

        self.assertEqual(actual_receipt["delta_distinct_seed_count"], 0)
        self.assertEqual(
            actual_receipt["pre_reconciled_delta_seed_ids"],
            ["API-IDENTITY-GET-USERS-READ-ALL-USERS-L407"],
        )
        self.assertEqual(actual_receipt["historical_prior_reconciled_units"], 3)
        self.assertEqual(actual_receipt["reconciled_behavior_units"], 4)
        self.assertEqual(actual_receipt["remaining_behavior_units"], 1)
        self.assertEqual(actual_receipt["pending_five"], 5)
        self.assertEqual(actual_receipt["units_since_reconciliation"], 5)
        self.assertFalse(actual_receipt["reconciliation_policy"]["global_reconciliation_complete"])
        self.assertTrue(
            actual_receipt["reconciliation_policy"][
                "no_sixth_behavior_accepted_before_complete_reconciliation"
            ]
        )
        self.assertFalse(actual_receipt["closures_authorized"])
        self.assertEqual(
            actual_receipt["remaining_inventory_paths"],
            ["docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"],
        )
        self.assertEqual(
            actual_receipt["remaining_behavior_descriptions"],
            {
                "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json": "manager-peer HTTP403"
            },
        )

    def test_c15_transition_rejections(self) -> None:
        receipt_path = (
            REPO_ROOT
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json"
        )
        prior_path = (
            REPO_ROOT
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json"
        )
        inv_path = (
            REPO_ROOT
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"
        )
        pred_path = (
            REPO_ROOT
            / "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json"
        )
        src_path = (
            REPO_ROOT
            / "tests/test_p1_00b_characterization_users_read_all_empty_http.py"
        )

        receipt_bytes = receipt_path.read_bytes()
        prior_bytes = prior_path.read_bytes()
        inv_bytes = inv_path.read_bytes()
        pred_bytes = pred_path.read_bytes()
        src_bytes = src_path.read_bytes()

        evidence_bytes = {
            "historical_checkpoint": prior_bytes,
            "source_inventory": inv_bytes,
            "predecessor_inventory": pred_bytes,
            "record": src_bytes,
        }

        actual_receipt = json.loads(receipt_bytes.decode("utf-8"))

        def _verify_reread_unchanged() -> None:
            self.assertEqual(receipt_path.read_bytes(), receipt_bytes)
            self.assertEqual(prior_path.read_bytes(), prior_bytes)
            self.assertEqual(inv_path.read_bytes(), inv_bytes)
            self.assertEqual(pred_path.read_bytes(), pred_bytes)
            self.assertEqual(src_path.read_bytes(), src_bytes)
            reloaded = json.loads(receipt_path.read_bytes().decode("utf-8"))
            assert_reconciliation_c15_from_c17_oracle(
                reloaded, evidence_bytes, test_case=self
            )

        assert_reconciliation_c15_from_c17_oracle(
            actual_receipt, evidence_bytes, test_case=self
        )

        # Case 1: C14 record substituted for C15 despite shared seed
        assert_reconciliation_c15_from_c17_oracle(
            actual_receipt, evidence_bytes, test_case=self
        )
        c14_mutated = copy.deepcopy(actual_receipt)
        c14_mutated["records"] = copy.deepcopy(FROZEN_C14_INVENTORY["records"])
        diff_keys_c14 = [k for k in actual_receipt if actual_receipt[k] != c14_mutated[k]]
        self.assertEqual(diff_keys_c14, ["records"])
        self.assertEqual(c14_mutated["records"], FROZEN_C14_INVENTORY["records"])
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                c14_mutated, evidence_bytes, test_case=self
            )
        _verify_reread_unchanged()

        # Case 2: Premature pending-five reset / global-complete
        assert_reconciliation_c15_from_c17_oracle(
            actual_receipt, evidence_bytes, test_case=self
        )
        reset_mutated = copy.deepcopy(actual_receipt)
        reset_mutated["pending_five"] = 0
        reset_mutated["reconciliation_policy"] = copy.deepcopy(
            actual_receipt["reconciliation_policy"]
        )
        reset_mutated["reconciliation_policy"]["global_reconciliation_complete"] = True
        diff_keys_reset = [k for k in actual_receipt if actual_receipt[k] != reset_mutated[k]]
        self.assertEqual(set(diff_keys_reset), {"pending_five", "reconciliation_policy"})
        self.assertEqual(reset_mutated["pending_five"], 0)
        self.assertTrue(reset_mutated["reconciliation_policy"]["global_reconciliation_complete"])
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                reset_mutated, evidence_bytes, test_case=self
            )
        alias_mutated_2 = copy.deepcopy(actual_receipt)
        alias_mutated_2["pending_five"] = True
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                alias_mutated_2, evidence_bytes, test_case=self
            )
        alias_mutated_2f = copy.deepcopy(actual_receipt)
        alias_mutated_2f["pending_five"] = 5.0
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                alias_mutated_2f, evidence_bytes, test_case=self
            )
        _verify_reread_unchanged()

        # Case 3: C15 falsely adding a distinct seed
        assert_reconciliation_c15_from_c17_oracle(
            actual_receipt, evidence_bytes, test_case=self
        )
        seed_mutated = copy.deepcopy(actual_receipt)
        seed_mutated["delta_distinct_seed_count"] = 1
        diff_keys_seed = [k for k in actual_receipt if actual_receipt[k] != seed_mutated[k]]
        self.assertEqual(diff_keys_seed, ["delta_distinct_seed_count"])
        self.assertEqual(seed_mutated["delta_distinct_seed_count"], 1)
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                seed_mutated, evidence_bytes, test_case=self
            )
        alias_mutated_3 = copy.deepcopy(actual_receipt)
        alias_mutated_3["delta_distinct_seed_count"] = False
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                alias_mutated_3, evidence_bytes, test_case=self
            )
        alias_mutated_3f = copy.deepcopy(actual_receipt)
        alias_mutated_3f["delta_distinct_seed_count"] = 0.0
        with self.assertRaises(AssertionError):
            assert_reconciliation_c15_from_c17_oracle(
                alias_mutated_3f, evidence_bytes, test_case=self
            )
        _verify_reread_unchanged()


if __name__ == "__main__":
    unittest.main()
