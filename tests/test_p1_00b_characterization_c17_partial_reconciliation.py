"""Hermetic characterization tests for continuation 17 partial reconciliation receipt.

Stage: P1-00b (IN_PROGRESS)
Assignment: WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161
Canonical gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work

Pure stdlib unittest with JSON/SHA/AST only: no subprocess, network, application
imports, or custom Session mocks.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import unittest

from tests.test_p1_00b_characterization_seed_handler_coverage import (
    HISTORICAL_SNAPSHOT_REL_PATH,
    load_historical_seed_coverage_snapshot,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

RECONCILIATION_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json"
)
HISTORICAL_CHECKPOINT_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json"
)
C17_INVENTORY_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
)
C16_INVENTORY_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
)
COVERAGE_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
C17_RECORD_RELPATH = (
    "tests/test_p1_00b_characterization_users_read_missing_http.py"
)

# Independently frozen full literal expected reconciliation receipt
FROZEN_EXPECTED_RECONCILIATION_17: dict[str, object] = {
    "schema_version": 1,
    "stage": "P1-00b",
    "assignment_id": "WA-P1-00B-AFTER-OBSERVER-PROJECTION-20261002-161",
    "inspected_head": "171c3d3cad480cf33bbe62232ac2fff75b21d3ff",
    "status": "PARTIAL_RECONCILIATION",
    "historical_checkpoint": {
        "last_commit": "5cbd4670983759b1f68b5c0fd78b77955b694e1f",
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json",
        "sha256": "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d",
    },
    "source_inventory": {
        "last_commit": "67931e960a1361ddb49f1be603eb1553cd1a0687",
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json",
        "sha256": "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15",
    },
    "records": [
        {
            "last_commit": "0f3deb613251c7c5f39822b2a1f0e5532e5e140f",
            "path": "tests/test_p1_00b_characterization_users_read_missing_http.py",
            "seed_ids": [
                "API-IDENTITY-GET-USERS-READ-USER-L440",
            ],
            "sha256": "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
            "test_selectors": [
                "tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get",
            ],
        },
    ],
    "units_since_reconciliation": 5,
    "total_behavior_units": 5,
    "pending_five": 5,
    "delta_reconciled_behavior_units": 1,
    "historical_prior_reconciled_units": 2,
    "reconciled_behavior_units": 3,
    "remaining_behavior_units": 2,
    "cumulative_inventory_paths": [
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json",
    ],
    "remaining_inventory_paths": [
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json",
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
    ],
    "remaining_behavior_descriptions": {
        "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json": "empty-list HTTP200",
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


def load_actual_reconciliation() -> dict[str, object]:
    raw_bytes = (REPO_ROOT / RECONCILIATION_RELPATH).read_bytes()
    return json.loads(raw_bytes.decode("utf-8"))


def assert_reconciliation_seventeen_oracle(
    data: dict[str, object],
    evidence_bytes: dict[str, bytes] | None = None,
) -> None:
    """Independently frozen literal oracle verifying receipt schema, types, equality, and evidence."""
    if type(data) is not dict:
        raise AssertionError(f"Top-level receipt must be a dict, got {type(data).__name__}")

    # 1. Exact schema_version type and value
    if "schema_version" not in data:
        raise AssertionError("Field 'schema_version' missing")
    sv = data["schema_version"]
    if type(sv) is not int:
        raise AssertionError(
            f"Field 'schema_version' must be exact int, got {type(sv).__name__} ({sv!r})"
        )
    if sv != 1:
        raise AssertionError(f"Field 'schema_version' must be 1, got {sv!r}")

    # 2. Check root integer counters with exact int type before equality
    root_counters = [
        ("units_since_reconciliation", 5),
        ("total_behavior_units", 5),
        ("pending_five", 5),
        ("delta_reconciled_behavior_units", 1),
        ("historical_prior_reconciled_units", 2),
        ("reconciled_behavior_units", 3),
        ("remaining_behavior_units", 2),
        ("origin_matrix_unresolved_cells", 24),
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

    # 3. Check nested counts counters with exact int type before equality
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

    # 4. Check nested seed_accounting prospective counter
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

    # 5. Full dictionary equality against frozen literal expected receipt
    if data != FROZEN_EXPECTED_RECONCILIATION_17:
        raise AssertionError("Receipt dictionary does not match frozen expected dictionary")

    # 6. Checkpoint, source inventory, and test record byte and AST resolution checks
    if evidence_bytes is None:
        evidence_bytes = {
            "checkpoint": (REPO_ROOT / data["historical_checkpoint"]["path"]).read_bytes(),
            "source_inventory": (REPO_ROOT / data["source_inventory"]["path"]).read_bytes(),
            "record": (REPO_ROOT / data["records"][0]["path"]).read_bytes(),
        }

    cp_sha = hashlib.sha256(evidence_bytes["checkpoint"]).hexdigest()
    if cp_sha != data["historical_checkpoint"]["sha256"]:
        raise AssertionError(
            f"Historical checkpoint SHA check failed: expected {data['historical_checkpoint']['sha256']}, got {cp_sha}"
        )

    src_sha = hashlib.sha256(evidence_bytes["source_inventory"]).hexdigest()
    if src_sha != data["source_inventory"]["sha256"]:
        raise AssertionError(
            f"Source inventory SHA check failed: expected {data['source_inventory']['sha256']}, got {src_sha}"
        )

    rec_sha = hashlib.sha256(evidence_bytes["record"]).hexdigest()
    if rec_sha != data["records"][0]["sha256"]:
        raise AssertionError(
            f"Record source SHA check failed: expected {data['records'][0]['sha256']}, got {rec_sha}"
        )

    try:
        tree = ast.parse(evidence_bytes["record"].decode("utf-8"))
    except Exception as exc:
        raise AssertionError(f"Record AST parse failed: {exc}") from exc

    selector = data["records"][0]["test_selectors"][0]
    sel_path, sel_class, sel_method = selector.split("::")
    class_node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == sel_class),
        None,
    )
    if class_node is None:
        raise AssertionError(f"Class {sel_class!r} not found in record AST")
    method_node = next(
        (
            n
            for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == sel_method
        ),
        None,
    )
    if method_node is None:
        raise AssertionError(f"Method {sel_method!r} not found in class {sel_class!r}")
    if isinstance(method_node, ast.AsyncFunctionDef):
        raise AssertionError(f"Method {sel_method!r} must be synchronous, but is async")


class C17PartialReconciliationTests(unittest.TestCase):
    """Test suite validating C17 partial reconciliation receipt and domain constraints."""

    def test_exact_receipt_and_evidence(self) -> None:
        """Verify receipt against literal oracle, source hashes, AST, and pending coverage bounds."""
        baseline = load_actual_reconciliation()
        assert_reconciliation_seventeen_oracle(baseline)

        # Verify immutable checkpoint bytes on disk
        cp_path = REPO_ROOT / HISTORICAL_CHECKPOINT_RELPATH
        self.assertTrue(cp_path.is_file(), f"Missing checkpoint file {cp_path}")
        cp_sha = hashlib.sha256(cp_path.read_bytes()).hexdigest()
        self.assertEqual(cp_sha, "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d")

        # Verify source continuation-17 inventory bytes on disk
        c17_path = REPO_ROOT / C17_INVENTORY_RELPATH
        self.assertTrue(c17_path.is_file(), f"Missing C17 inventory file {c17_path}")
        c17_sha = hashlib.sha256(c17_path.read_bytes()).hexdigest()
        self.assertEqual(c17_sha, "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15")

        # Verify C17 behavior test bytes on disk
        rec_path = REPO_ROOT / C17_RECORD_RELPATH
        self.assertTrue(rec_path.is_file(), f"Missing C17 record file {rec_path}")
        rec_sha = hashlib.sha256(rec_path.read_bytes()).hexdigest()
        self.assertEqual(rec_sha, "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb")

        # Verify pending continuation-16 overlap with C17 seed
        c16_path = REPO_ROOT / C16_INVENTORY_RELPATH
        self.assertTrue(c16_path.is_file(), f"Missing C16 inventory file {c16_path}")
        c16_data = json.loads(c16_path.read_bytes().decode("utf-8"))
        c16_seeds = set(c16_data["records"][0]["seed_ids"])
        self.assertIn("API-IDENTITY-GET-USERS-READ-USER-L440", c16_seeds)

        # Verify historical seed coverage snapshot remains frozen at 387/465 PARTIAL
        cov_data = load_historical_seed_coverage_snapshot(
            REPO_ROOT / HISTORICAL_SNAPSHOT_REL_PATH
        )
        self.assertEqual(cov_data["counts"]["baseline_evidenced_seed_count"], 135)
        self.assertEqual(cov_data["counts"]["reconciled_new_seed_count"], 252)
        self.assertEqual(cov_data["counts"]["evidenced_seed_count"], 387)
        self.assertEqual(cov_data["counts"]["remaining_seed_count"], 78)
        self.assertEqual(cov_data["inventory"]["expected_seed_count"], 465)
        self.assertEqual(cov_data["coverage_status"], "PARTIAL")
        self.assertEqual(
            cov_data["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )

        # Verify exact policy, path, exclusion assertions
        self.assertEqual(baseline["stage"], "P1-00b")
        self.assertEqual(baseline["status"], "PARTIAL_RECONCILIATION")
        self.assertEqual(baseline["coverage_status"], "PARTIAL")
        self.assertEqual(baseline["seed_acceptance_status"], "FULL_SEED_ACCEPTANCE_NOT_ASSESSED")
        self.assertFalse(baseline["closures_authorized"])
        self.assertEqual(baseline["origin_matrix_unresolved_cells"], 24)
        self.assertEqual(baseline["stages"], {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"})
        self.assertEqual(
            baseline["reconciliation_policy"],
            {
                "no_sixth_behavior_accepted_before_complete_reconciliation": True,
                "deferred_continuations": ["continuation-12"],
                "deferred_proposals": [
                    "153/154/155/157-C001",
                    "normal159 C1/C2",
                    "rejected C15 machine checkpoint",
                    "rejected policy/seed proposals",
                ],
                "global_reconciliation_complete": False,
            },
        )

    def test_receipt_drift_rejected(self) -> None:
        """Verify oracle rejects mutants across drift, overlap, deferrals, closure, and corrupted bytes."""
        baseline = load_actual_reconciliation()

        drift_scenarios = [
            ("wrong_record_commit", lambda d: d["records"][0].__setitem__("last_commit", "0" * 40)),
            ("wrong_record_hash", lambda d: d["records"][0].__setitem__("sha256", "1" * 64)),
            (
                "wrong_record_selector",
                lambda d: d["records"][0].__setitem__(
                    "test_selectors",
                    ["tests/test_p1_00b_characterization_users_read_missing_http.py::UsersReadMissingHttpCharacterizationTests::test_other"],
                ),
            ),
            (
                "wrong_record_seed",
                lambda d: d["records"][0].__setitem__("seed_ids", ["API-IDENTITY-WRONG-SEED"]),
            ),
            (
                "duplicate_record",
                lambda d: d["records"].append(copy.deepcopy(d["records"][0])),
            ),
            (
                "mixed_inventory_record",
                lambda d: d["records"].append({
                    "last_commit": "05fe0d5c2a5dae727aa7219549843b1ce6235945",
                    "path": "tests/test_p1_00b_characterization_users_read_manager_peer_http.py",
                    "seed_ids": ["API-IDENTITY-GET-USERS-READ-USER-L440"],
                    "sha256": "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd",
                    "test_selectors": ["tests/test_p1_00b_characterization_users_read_manager_peer_http.py::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading"],
                }),
            ),
            (
                "wrong_cumulative_inventories",
                lambda d: d["cumulative_inventory_paths"].pop(),
            ),
            (
                "wrong_remaining_inventories",
                lambda d: d["remaining_inventory_paths"].append(
                    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
                ),
            ),
            (
                "lost_deferred_continuation",
                lambda d: d["reconciliation_policy"].__setitem__("deferred_continuations", []),
            ),
            (
                "lost_deferred_proposals",
                lambda d: d["reconciliation_policy"].__setitem__("deferred_proposals", []),
            ),
            (
                "premature_global_reconciliation_complete",
                lambda d: d["reconciliation_policy"].__setitem__("global_reconciliation_complete", True),
            ),
            (
                "premature_closures_authorized",
                lambda d: d.__setitem__("closures_authorized", True),
            ),
            (
                "premature_coverage_status_complete",
                lambda d: d.__setitem__("coverage_status", "COMPLETE"),
            ),
            (
                "lost_seed_overlap_distinction",
                lambda d: d["seed_accounting"].__setitem__("overlapping_pending_seeds", []),
            ),
            (
                "premature_canonical_increment",
                lambda d: d["counts"].__setitem__("evidenced_seed_count", 388),
            ),
        ]

        for case_name, mutator in drift_scenarios:
            with self.subTest(case=case_name):
                assert_reconciliation_seventeen_oracle(baseline)
                mutant = copy.deepcopy(baseline)
                mutator(mutant)
                with self.assertRaises(AssertionError):
                    assert_reconciliation_seventeen_oracle(mutant)
                fresh_baseline = load_actual_reconciliation()
                assert_reconciliation_seventeen_oracle(fresh_baseline)

        # Byte corruption subtests verifying altered actual evidence bytes raise AssertionError
        valid_evidence = {
            "checkpoint": (REPO_ROOT / HISTORICAL_CHECKPOINT_RELPATH).read_bytes(),
            "source_inventory": (REPO_ROOT / C17_INVENTORY_RELPATH).read_bytes(),
            "record": (REPO_ROOT / C17_RECORD_RELPATH).read_bytes(),
        }

        byte_corruption_cases = [
            ("corrupted_checkpoint_bytes", "checkpoint"),
            ("corrupted_source_inventory_bytes", "source_inventory"),
            ("corrupted_record_source_bytes", "record"),
        ]
        for case_name, target_key in byte_corruption_cases:
            with self.subTest(case=case_name):
                assert_reconciliation_seventeen_oracle(baseline, evidence_bytes=valid_evidence)
                corrupted = dict(valid_evidence)
                corrupted[target_key] = valid_evidence[target_key] + b"\n"
                with self.assertRaises(AssertionError):
                    assert_reconciliation_seventeen_oracle(baseline, evidence_bytes=corrupted)
                assert_reconciliation_seventeen_oracle(baseline, evidence_bytes=valid_evidence)

    def test_pending_accounting_types_rejected(self) -> None:
        """Verify oracle rejects bool, float, string, and null aliases for schema and all counters."""
        baseline = load_actual_reconciliation()

        counter_definitions = [
            ("schema_version", None, None, 1),
            ("units_since_reconciliation", None, None, 5),
            ("total_behavior_units", None, None, 5),
            ("pending_five", None, None, 5),
            ("delta_reconciled_behavior_units", None, None, 1),
            ("historical_prior_reconciled_units", None, None, 2),
            ("reconciled_behavior_units", None, None, 3),
            ("remaining_behavior_units", None, None, 2),
            ("origin_matrix_unresolved_cells", None, None, 24),
            ("counts.baseline_evidenced_seed_count", "counts", "baseline_evidenced_seed_count", 135),
            ("counts.reconciled_new_seed_count", "counts", "reconciled_new_seed_count", 252),
            ("counts.evidenced_seed_count", "counts", "evidenced_seed_count", 387),
            ("counts.remaining_seed_count", "counts", "remaining_seed_count", 78),
            ("counts.expected_seed_count", "counts", "expected_seed_count", 465),
            (
                "seed_accounting.prospective_distinct_seed_count",
                "seed_accounting",
                "prospective_distinct_seed_count",
                1,
            ),
        ]

        for full_name, parent_key, nested_key, expected_val in counter_definitions:
            aliases = [True, False, float(expected_val), str(expected_val), None]
            for bad_alias in aliases:
                with self.subTest(field=full_name, alias_type=type(bad_alias).__name__, alias=bad_alias):
                    assert_reconciliation_seventeen_oracle(baseline)
                    mutant = copy.deepcopy(baseline)
                    if parent_key is None:
                        mutant[full_name] = bad_alias
                    else:
                        mutant[parent_key][nested_key] = bad_alias
                    with self.assertRaises(AssertionError):
                        assert_reconciliation_seventeen_oracle(mutant)
                    fresh_baseline = load_actual_reconciliation()
                    assert_reconciliation_seventeen_oracle(fresh_baseline)


if __name__ == "__main__":
    unittest.main()
