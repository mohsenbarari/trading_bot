from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    C13_SEED,
    C14_SEED,
    compute_canonical_accounting,
    freeze_evidence_manifest,
)
from tests.test_p1_00b_characterization_c16_c17_shared_seed_contribution import (
    C15_CHECKPOINT_COMMIT,
    C15_CHECKPOINT_RELPATH,
    C16_BEHAVIOR_COMMIT,
    C16_RECORD_PATH,
    C16_RECORD_SHA,
    C16_RELPATH,
    C16_TEST_SELECTOR,
    SHARED_SEED_ID,
    assert_c16_c17_shared_seed_contribution,
    assert_complete_closure_unchanged,
)
from tests.test_p1_00b_characterization_c16_evidence_closure_snapshot import (
    assert_c16_evidence_closure_unchanged,
    freeze_c16_evidence_closure,
)
from tests.test_p1_00b_characterization_c16_pinned_helper_restoration import (
    freeze_pinned_helper_manifest,
    merge_complete_snapshot,
)

EXPECTED_SUCCESSOR_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c16-from-c15.json"
)
EXPECTED_ASSIGNMENT_ID = "WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169"
EXPECTED_HEAD = "8e5590383b40c46c4b5c2b97c20208b0bfb650a7"
EXPECTED_C16_INVENTORY_SHA = "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a"
EXPECTED_C16_INVENTORY_COMMIT = "0eebdb41c0300ae0d92a16d5cd0aa8db75ad2b9f"
EXPECTED_C15_CHECKPOINT_SHA = "b7dac667be1584c50743b24d806d2b9d1c6fe50a85ec1e9936a65ed96754ffbb"


class C16SuccessorReconciliationTests(unittest.TestCase):
    def test_c16_successor_reconciles_accepted_unit_and_preserves_open_gates(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent

        canonical_snap = freeze_evidence_manifest(repo_root)
        c16_snap = freeze_c16_evidence_closure(repo_root)
        helper_snap = freeze_pinned_helper_manifest(repo_root)
        complete_snapshot = merge_complete_snapshot(canonical_snap, c16_snap, helper_snap)

        successor_path = repo_root / EXPECTED_SUCCESSOR_RELPATH
        self.assertTrue(successor_path.is_file(), f"Successor receipt missing at {successor_path}")
        raw_successor = successor_path.read_bytes()
        successor_data = json.loads(raw_successor.decode("utf-8"))

        self.assertEqual(successor_data.get("schema_version"), 1)
        self.assertEqual(successor_data.get("stage"), "P1-00b")
        self.assertEqual(successor_data.get("assignment_id"), EXPECTED_ASSIGNMENT_ID)
        self.assertEqual(successor_data.get("inspected_head"), EXPECTED_HEAD)
        self.assertEqual(successor_data.get("status"), "PARTIAL_RECONCILIATION")

        hist_cp = successor_data.get("historical_checkpoint", {})
        self.assertEqual(hist_cp.get("path"), C15_CHECKPOINT_RELPATH)
        self.assertEqual(hist_cp.get("last_commit"), C15_CHECKPOINT_COMMIT)
        self.assertEqual(hist_cp.get("sha256"), EXPECTED_C15_CHECKPOINT_SHA)
        actual_c15_sha = hashlib.sha256(complete_snapshot[C15_CHECKPOINT_RELPATH]).hexdigest()
        self.assertEqual(actual_c15_sha, EXPECTED_C15_CHECKPOINT_SHA)
        self.assertEqual(hist_cp.get("sha256"), actual_c15_sha)

        src_inv = successor_data.get("source_inventory", {})
        self.assertEqual(src_inv.get("path"), C16_RELPATH)
        self.assertEqual(src_inv.get("last_commit"), EXPECTED_C16_INVENTORY_COMMIT)
        self.assertEqual(src_inv.get("sha256"), EXPECTED_C16_INVENTORY_SHA)
        actual_c16_inv_sha = hashlib.sha256(complete_snapshot[C16_RELPATH]).hexdigest()
        self.assertEqual(actual_c16_inv_sha, EXPECTED_C16_INVENTORY_SHA)
        self.assertEqual(src_inv.get("sha256"), actual_c16_inv_sha)

        records = successor_data.get("records", [])
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec.get("path"), C16_RECORD_PATH)
        self.assertEqual(rec.get("last_commit"), C16_BEHAVIOR_COMMIT)
        self.assertEqual(rec.get("sha256"), C16_RECORD_SHA)
        self.assertEqual(rec.get("seed_ids"), [SHARED_SEED_ID])
        self.assertEqual(rec.get("test_selectors"), [C16_TEST_SELECTOR])
        actual_record_sha = hashlib.sha256(complete_snapshot[C16_RECORD_PATH]).hexdigest()
        self.assertEqual(actual_record_sha, C16_RECORD_SHA)
        self.assertEqual(rec.get("sha256"), actual_record_sha)

        self.assertEqual(successor_data.get("total_behavior_units"), 5)
        self.assertEqual(successor_data.get("pending_five"), 5)
        self.assertEqual(successor_data.get("units_since_reconciliation"), 5)
        self.assertEqual(successor_data.get("historical_prior_reconciled_units"), 4)
        self.assertEqual(successor_data.get("delta_reconciled_behavior_units"), 1)
        self.assertEqual(successor_data.get("reconciled_behavior_units"), 5)
        self.assertEqual(successor_data.get("remaining_behavior_units"), 0)
        self.assertEqual(successor_data.get("delta_distinct_seed_count"), 0)

        expected_cumulative = [
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json",
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json",
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json",
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json",
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json",
        ]
        self.assertEqual(successor_data.get("cumulative_inventory_paths"), expected_cumulative)
        self.assertEqual(successor_data.get("remaining_inventory_paths"), [])
        self.assertEqual(successor_data.get("remaining_behavior_descriptions"), {})

        policy = successor_data.get("reconciliation_policy", {})
        self.assertFalse(policy.get("global_reconciliation_complete"))
        self.assertTrue(policy.get("no_sixth_behavior_accepted_before_complete_reconciliation"))
        self.assertIn("continuation-12", policy.get("deferred_continuations", []))
        deferred_proposals = policy.get("deferred_proposals", [])
        self.assertIn("defer_prior_unit=normal169-c16-receipt-custody-20261003-c001", deferred_proposals)
        self.assertIn("defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2", deferred_proposals)

        (
            _per_file_counts,
            surface_seeds,
            baseline_seeds,
            new_beyond_baseline,
            evidenced_seeds,
            remaining_seeds,
        ) = compute_canonical_accounting(complete_snapshot)

        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(new_beyond_baseline), 252)
        self.assertEqual(len(evidenced_seeds), 387)
        self.assertEqual(len(remaining_seeds), 78)

        counts = successor_data.get("counts", {})
        self.assertEqual(counts.get("baseline_evidenced_seed_count"), 135)
        self.assertEqual(counts.get("reconciled_new_seed_count"), 252)
        self.assertEqual(counts.get("evidenced_seed_count"), 387)
        self.assertEqual(counts.get("remaining_seed_count"), 78)
        self.assertEqual(counts.get("expected_seed_count"), 465)

        c13_doc = json.loads(complete_snapshot["docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"].decode("utf-8"))
        c14_doc = json.loads(complete_snapshot["docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json"].decode("utf-8"))
        c15_doc = json.loads(complete_snapshot["docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json"].decode("utf-8"))
        c16_doc = json.loads(complete_snapshot[C16_RELPATH].decode("utf-8"))
        c17_doc = json.loads(complete_snapshot["docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"].decode("utf-8"))

        c13_seeds = {s for r in c13_doc.get("records", []) for s in r.get("seed_ids", [])}
        c14_seeds = {s for r in c14_doc.get("records", []) for s in r.get("seed_ids", [])}
        c15_seeds = {s for r in c15_doc.get("records", []) for s in r.get("seed_ids", [])}
        c16_seeds = {s for r in c16_doc.get("records", []) for s in r.get("seed_ids", [])}
        c17_seeds = {s for r in c17_doc.get("records", []) for s in r.get("seed_ids", [])}

        self.assertEqual(c13_seeds, {C13_SEED})
        self.assertEqual(c14_seeds, {C14_SEED})
        self.assertEqual(c15_seeds, {C14_SEED})
        self.assertEqual(c16_seeds, {SHARED_SEED_ID})
        self.assertEqual(c17_seeds, {SHARED_SEED_ID})

        self.assertIn(C14_SEED, evidenced_seeds)
        self.assertNotIn(C13_SEED, evidenced_seeds)
        self.assertNotIn(SHARED_SEED_ID, evidenced_seeds)

        shared_union = c16_seeds | c17_seeds
        shared_intersection = c16_seeds & c17_seeds
        self.assertEqual(shared_intersection, {SHARED_SEED_ID})
        self.assertEqual(len(shared_union - evidenced_seeds), 1)

        c16_incremental_after_c17 = c16_seeds - (evidenced_seeds | c17_seeds)
        self.assertEqual(c16_incremental_after_c17, set())

        group_union = c13_seeds | c14_seeds | c15_seeds | c16_seeds | c17_seeds
        prospective_five_unit_union = group_union - evidenced_seeds
        self.assertEqual(prospective_five_unit_union, {C13_SEED, SHARED_SEED_ID})
        self.assertEqual(len(prospective_five_unit_union), 2)

        scoped_acc = successor_data.get("seed_accounting", {})
        self.assertEqual(scoped_acc.get("prospective_distinct_seed_count"), 1)
        self.assertEqual(scoped_acc.get("prospective_distinct_seeds"), [SHARED_SEED_ID])

        five_unit_acc = successor_data.get("five_unit_seed_accounting", {})
        self.assertEqual(five_unit_acc.get("prospective_distinct_seed_count"), 2)
        self.assertEqual(set(five_unit_acc.get("prospective_distinct_seeds", [])), {C13_SEED, SHARED_SEED_ID})
        self.assertEqual(five_unit_acc.get("already_canonical_seeds"), [C14_SEED])

        assert_c16_c17_shared_seed_contribution(successor_data, complete_snapshot, c16_doc, c17_doc)

        two_seed_group_view = {
            "seed_accounting": {
                "prospective_distinct_seed_count": 2,
                "prospective_distinct_seeds": [SHARED_SEED_ID, C13_SEED],
            }
        }
        with self.assertRaises(ValueError):
            assert_c16_c17_shared_seed_contribution(two_seed_group_view, complete_snapshot, c16_doc, c17_doc)

        self.assertEqual(successor_data.get("coverage_status"), "PARTIAL")
        self.assertEqual(successor_data.get("seed_acceptance_status"), "FULL_SEED_ACCEPTANCE_NOT_ASSESSED")
        self.assertEqual(
            successor_data.get("canonical_blockers"),
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )
        self.assertEqual(
            successor_data.get("stages"),
            {
                "P1-00": "COMPLETE",
                "P1-00b": "IN_PROGRESS",
            },
        )
        self.assertEqual(successor_data.get("origin_matrix_unresolved_cells"), 24)
        self.assertFalse(successor_data.get("closures_authorized"))

        expected_exclusions = [
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
        ]
        self.assertEqual(successor_data.get("exclusions"), expected_exclusions)

        assert_complete_closure_unchanged(repo_root, complete_snapshot)
        assert_c16_evidence_closure_unchanged(repo_root, c16_snap)


if __name__ == "__main__":
    unittest.main()
