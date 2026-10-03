from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    compute_canonical_accounting,
    freeze_evidence_manifest,
)
from tests.test_p1_00b_characterization_recent_inventory_continuation_13_self_export import (
    EXPECTED_PREDECESSOR_LAST_COMMIT,
    EXPECTED_PREDECESSOR_PATH,
    EXPECTED_PREDECESSOR_SHA,
    EXPECTED_RECORD_LAST_COMMIT,
    EXPECTED_RECORD_PATH,
    EXPECTED_RECORD_SHA,
    EXPECTED_SCHEMA_VERSION,
    EXPECTED_SEED_IDS,
    EXPECTED_STAGE,
    EXPECTED_TEST_SELECTORS,
    validate_continuation_thirteen_provenance,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

LIVE_MANIFEST_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
EXPECTED_LIVE_MANIFEST_SHA256 = (
    "da0fd31c0dbc4776b548a8b235f8479fe8e60687ff51e0cb2623c3c21d4c42d7"
)
EXPECTED_LIVE_MANIFEST_BYTE_LEN = 2420

C13_PROMOTION_RECEIPT_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-c13-canonical-promotion.json"
)
CONTINUATION_13_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"
)

EXPECTED_C13_SEED = "API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797"
EXPECTED_SHARED_READ_USER_SEED = "API-IDENTITY-GET-USERS-READ-USER-L440"

EXPECTED_LIVE_MANIFEST: dict[str, object] = {
    "schema_version": 1,
    "stage": "P1-00b",
    "assignment_id": "WA-P1-00B-FREEZE-20260904-001",
    "inventory": {
        "expected_seed_count": 465,
        "paths": [
            "docs/refactor/two-server/section-1/inventory/surface-api.json",
            "docs/refactor/two-server/section-1/inventory/surface-bot.json",
            "docs/refactor/two-server/section-1/inventory/surface-web.json",
            "docs/refactor/two-server/section-1/inventory/surface-jobs.json",
        ],
    },
    "evidence_sources": {
        "baseline_path": "docs/refactor/two-server/section-1/inventory/scenario-records.json",
        "base_path": "docs/refactor/two-server/section-1/inventory/recent-characterization.json",
        "continuation_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation.json",
        "continuation_2_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-2.json",
        "continuation_3_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-3.json",
        "continuation_4_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-4.json",
        "continuation_5_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-5.json",
        "continuation_6_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-6.json",
        "continuation_7_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-7.json",
        "continuation_8_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-8.json",
        "continuation_9_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-9.json",
        "continuation_10_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-10.json",
        "continuation_11_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-11.json",
        "continuation_13_path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json",
    },
    "counts": {
        "baseline_evidenced_seed_count": 135,
        "reconciled_new_seed_count": 253,
        "evidenced_seed_count": 388,
        "remaining_seed_count": 77,
    },
    "coverage_status": "PARTIAL",
    "canonical_blockers": [
        "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
        "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
    ],
}

EXPECTED_PROMOTION_RECEIPT: dict[str, object] = {
    "schema_version": 1,
    "stage": "P1-00b",
    "assignment_id": "WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169",
    "inspected_head": "b8f2aea4367f3f08195e32522396f980351057cf",
    "status": "PARTIAL_CANONICAL_INTEGRATION",
    "source_inventory": {
        "path": "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json",
        "sha256": "ebe8e3c3189f8961a61d256ed5cc690864c2e4d7f5c6d1cc91e3554060769071",
    },
    "records": [
        {
            "path": "tests/test_p1_00b_characterization_trade_export_self_delegation.py",
            "sha256": "b8db918415014c2fbaec56888bdc37e6be53f329829a6a1af9dd0ba72b5856d1",
            "last_commit": "98c98e93e94aa426ebae7502a67a5d5053a3a31a",
            "seed_ids": [
                "API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797",
            ],
            "test_selectors": [
                "tests/test_p1_00b_characterization_trade_export_self_delegation.py::TradeExportSelfDelegationCharacterizationTests::test_self_target_delegates_exact_filters_and_returns_export_response",
            ],
        },
    ],
    "live_manifest": {
        "path": "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json",
        "sha256": "da0fd31c0dbc4776b548a8b235f8479fe8e60687ff51e0cb2623c3c21d4c42d7",
    },
    "promoted_seed_ids": [
        "API-TRADE-GET-TRADES-EXPORT-TRADES-WITH-USER-L5797",
    ],
    "delta_distinct_seed_count": 1,
    "pending_canonical_seed_ids": [
        "API-IDENTITY-GET-USERS-READ-USER-L440",
    ],
    "units_since_reconciliation": 5,
    "total_behavior_units": 5,
    "pending_five": 5,
    "reconciled_behavior_units": 5,
    "remaining_behavior_units": 0,
    "global_reconciliation_complete": False,
    "counts": {
        "baseline_evidenced_seed_count": 135,
        "reconciled_new_seed_count": 253,
        "evidenced_seed_count": 388,
        "remaining_seed_count": 77,
        "expected_seed_count": 465,
    },
    "coverage_status": "PARTIAL",
    "seed_acceptance_status": "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
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
    "deferrals": [
        "defer_prior_unit=normal169-canonical-manifest-successor-20261003-c010-v2",
        "defer_prior_unit=normal169-c17-consumer-loader-integration-20261003-c007",
        "defer_prior_unit=normal169-c17-historical-coverage-reader-isolation-20261003-c005",
        "defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004",
        "defer_prior_unit=normal169-c16-receipt-custody-20261003-c001",
        "defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2",
    ],
    "exclusions": [
        "shared C16/C17 canonical promotion and ledger integration",
        "read_user self-success",
        "super-admin rejection",
        "authorization policy coverage",
        "new or sixth product behavior",
        "mutation,persistence,retry,reload",
        "origin/seed-blocker/Stage closure",
    ],
}


class C13CanonicalPromotionTests(unittest.TestCase):
    """Characterization test witness verifying C13 canonical promotion and gate retention."""

    def test_c13_canonical_promotion_retains_shared_seed_gate(self) -> None:
        """Verify C13 promotion manifest custody, set delta, and gate retention."""
        live_path = REPO_ROOT / LIVE_MANIFEST_RELPATH
        self.assertTrue(live_path.is_file(), f"Missing live manifest: {live_path}")
        live_bytes = live_path.read_bytes()
        self.assertEqual(len(live_bytes), EXPECTED_LIVE_MANIFEST_BYTE_LEN)
        self.assertEqual(
            hashlib.sha256(live_bytes).hexdigest(),
            EXPECTED_LIVE_MANIFEST_SHA256,
        )

        live_doc = json.loads(live_bytes.decode("utf-8"))
        self.assertEqual(live_doc, EXPECTED_LIVE_MANIFEST)

        # Validate C13 inventory provenance and AST selector resolution
        c13_inv_path = REPO_ROOT / CONTINUATION_13_RELPATH
        self.assertTrue(c13_inv_path.is_file(), f"Missing C13 inventory: {c13_inv_path}")
        c13_inv = json.loads(c13_inv_path.read_bytes().decode("utf-8"))

        pred_path = REPO_ROOT / EXPECTED_PREDECESSOR_PATH
        self.assertTrue(pred_path.is_file(), f"Missing predecessor inventory: {pred_path}")
        pred_bytes = pred_path.read_bytes()

        src_path = REPO_ROOT / EXPECTED_RECORD_PATH
        self.assertTrue(src_path.is_file(), f"Missing record source file: {src_path}")
        src_bytes = src_path.read_bytes()

        validate_continuation_thirteen_provenance(c13_inv, pred_bytes, src_bytes)

        self.assertEqual(c13_inv["schema_version"], EXPECTED_SCHEMA_VERSION)
        self.assertEqual(c13_inv["stage"], EXPECTED_STAGE)
        self.assertEqual(c13_inv["predecessor_inventory"]["path"], EXPECTED_PREDECESSOR_PATH)
        self.assertEqual(c13_inv["predecessor_inventory"]["sha256"], EXPECTED_PREDECESSOR_SHA)
        self.assertEqual(
            c13_inv["predecessor_inventory"]["last_commit"],
            EXPECTED_PREDECESSOR_LAST_COMMIT,
        )
        self.assertEqual(len(c13_inv["records"]), 1)
        rec = c13_inv["records"][0]
        self.assertEqual(rec["path"], EXPECTED_RECORD_PATH)
        self.assertEqual(rec["sha256"], EXPECTED_RECORD_SHA)
        self.assertEqual(rec["last_commit"], EXPECTED_RECORD_LAST_COMMIT)
        self.assertEqual(rec["seed_ids"], EXPECTED_SEED_IDS)
        self.assertEqual(rec["test_selectors"], EXPECTED_TEST_SELECTORS)

        # Historical set accounting over 23 frozen head files
        frozen_manifest = freeze_evidence_manifest(REPO_ROOT)
        (
            per_file_counts,
            surface_seeds,
            baseline_seeds,
            new_beyond_baseline,
            evidenced_seeds,
            remaining_seeds,
        ) = compute_canonical_accounting(frozen_manifest)

        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(new_beyond_baseline), 252)
        self.assertEqual(len(evidenced_seeds), 387)
        self.assertEqual(len(remaining_seeds), 78)

        # Prior to live C13 promotion, C13 and shared read_user are absent from evidenced set
        self.assertNotIn(EXPECTED_C13_SEED, evidenced_seeds)
        self.assertNotIn(EXPECTED_SHARED_READ_USER_SEED, evidenced_seeds)
        self.assertIn(EXPECTED_C13_SEED, remaining_seeds)
        self.assertIn(EXPECTED_SHARED_READ_USER_SEED, remaining_seeds)

        # Live union adds exactly C13, leaving shared read_user pending
        c13_seed_set = set(rec["seed_ids"])
        self.assertEqual(c13_seed_set, {EXPECTED_C13_SEED})

        live_evidenced = evidenced_seeds | c13_seed_set
        delta_seeds = live_evidenced - evidenced_seeds
        self.assertEqual(delta_seeds, {EXPECTED_C13_SEED})
        self.assertEqual(len(delta_seeds), 1)

        live_new_beyond_baseline = live_evidenced - baseline_seeds
        live_remaining = surface_seeds - live_evidenced

        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(live_new_beyond_baseline), 253)
        self.assertEqual(len(live_evidenced), 388)
        self.assertEqual(len(live_remaining), 77)

        self.assertNotIn(EXPECTED_SHARED_READ_USER_SEED, live_evidenced)
        self.assertIn(EXPECTED_SHARED_READ_USER_SEED, live_remaining)

        # Verify live manifest counts match computed accounting
        self.assertEqual(
            live_doc["counts"]["baseline_evidenced_seed_count"],
            len(baseline_seeds),
        )
        self.assertEqual(
            live_doc["counts"]["reconciled_new_seed_count"],
            len(live_new_beyond_baseline),
        )
        self.assertEqual(
            live_doc["counts"]["evidenced_seed_count"],
            len(live_evidenced),
        )
        self.assertEqual(
            live_doc["counts"]["remaining_seed_count"],
            len(live_remaining),
        )
        self.assertEqual(
            live_doc["inventory"]["expected_seed_count"],
            len(surface_seeds),
        )

        # Verify C13 canonical promotion receipt on disk
        receipt_path = REPO_ROOT / C13_PROMOTION_RECEIPT_RELPATH
        self.assertTrue(receipt_path.is_file(), f"Missing receipt: {receipt_path}")
        receipt_bytes = receipt_path.read_bytes()
        self.assertLessEqual(
            len(receipt_bytes),
            5000,
            f"Receipt size {len(receipt_bytes)} exceeds 5000 bytes",
        )
        receipt_doc = json.loads(receipt_bytes.decode("utf-8"))
        self.assertEqual(receipt_doc, EXPECTED_PROMOTION_RECEIPT)

        # Assert key receipt contract invariants
        self.assertEqual(receipt_doc["schema_version"], 1)
        self.assertEqual(receipt_doc["stage"], "P1-00b")
        self.assertEqual(
            receipt_doc["assignment_id"],
            "WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169",
        )
        self.assertEqual(
            receipt_doc["inspected_head"],
            "b8f2aea4367f3f08195e32522396f980351057cf",
        )
        self.assertEqual(receipt_doc["status"], "PARTIAL_CANONICAL_INTEGRATION")
        self.assertEqual(receipt_doc["promoted_seed_ids"], [EXPECTED_C13_SEED])
        self.assertEqual(receipt_doc["delta_distinct_seed_count"], 1)
        self.assertEqual(
            receipt_doc["pending_canonical_seed_ids"],
            [EXPECTED_SHARED_READ_USER_SEED],
        )
        self.assertEqual(receipt_doc["units_since_reconciliation"], 5)
        self.assertEqual(receipt_doc["total_behavior_units"], 5)
        self.assertEqual(receipt_doc["pending_five"], 5)
        self.assertEqual(receipt_doc["reconciled_behavior_units"], 5)
        self.assertEqual(receipt_doc["remaining_behavior_units"], 0)
        self.assertFalse(receipt_doc["global_reconciliation_complete"])
        self.assertEqual(receipt_doc["coverage_status"], "PARTIAL")
        self.assertEqual(
            receipt_doc["seed_acceptance_status"],
            "FULL_SEED_ACCEPTANCE_NOT_ASSESSED",
        )
        self.assertEqual(
            receipt_doc["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )
        self.assertEqual(
            receipt_doc["stages"],
            {"P1-00": "COMPLETE", "P1-00b": "IN_PROGRESS"},
        )
        self.assertEqual(receipt_doc["origin_matrix_unresolved_cells"], 24)
        self.assertFalse(receipt_doc["closures_authorized"])
        self.assertEqual(len(receipt_doc["deferrals"]), 6)


if __name__ == "__main__":
    unittest.main()
