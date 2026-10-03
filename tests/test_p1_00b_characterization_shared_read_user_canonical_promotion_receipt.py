from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import unittest

TARGET_RECEIPT_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-shared-read-user-canonical-promotion.json"
)

EXPECTED_HEAD: str = "65772a50b3729a9ec905a6b542ac27ced057e5f0"
EXPECTED_ASSIGNMENT: str = "WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169"
EXPECTED_STAGE: str = "P1-00b"
SHARED_SEED_ID: str = "API-IDENTITY-GET-USERS-READ-USER-L440"

LIVE_MANIFEST_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
LIVE_MANIFEST_SHA: str = (
    "3e6b945f82907b24905e168f05f5c58745b5cecaa232abf4fa71997408e9ee0e"
)

WITNESS_RELPATH: str = (
    "tests/test_p1_00b_characterization_shared_read_user_live_set_promotion.py"
)
WITNESS_SHA: str = (
    "d54a50ef5f234d432c0fdc85de0f24e049d05d84fc77971696918aacca532601"
)
WITNESS_SELECTOR: str = (
    "tests/test_p1_00b_characterization_shared_read_user_live_set_promotion.py::SharedReadUserLiveSetPromotionTests::test_live_manifest_promotes_exactly_one_shared_seed_with_custody"
)

SNAPSHOT388_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-388-snapshot.json"
)
SNAPSHOT388_SHA: str = (
    "da0fd31c0dbc4776b548a8b235f8479fe8e60687ff51e0cb2623c3c21d4c42d7"
)

C13_RECEIPT_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-c13-canonical-promotion.json"
)
C13_RECEIPT_SHA: str = (
    "5ab85485719dfe5635ed9c106082b0453108336b5791f6abc51c4c5bf2f8430b"
)

C16_RECON_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c16-from-c15.json"
)
C16_RECON_SHA: str = (
    "956103bddc94004da7a80dd4a73f9fc4dd05fde25c7dea3d126d8da6c80d5cce"
)

C16_INVENTORY_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
)
C16_INVENTORY_SHA: str = (
    "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a"
)
C16_SOURCE_RELPATH: str = (
    "tests/test_p1_00b_characterization_users_read_manager_peer_http.py"
)
C16_SOURCE_SHA: str = (
    "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd"
)

C17_INVENTORY_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
)
C17_INVENTORY_SHA: str = (
    "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15"
)
C17_SOURCE_RELPATH: str = (
    "tests/test_p1_00b_characterization_users_read_missing_http.py"
)
C17_SOURCE_SHA: str = (
    "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb"
)

EVIDENCE_PINS: dict[str, str] = {
    LIVE_MANIFEST_RELPATH: LIVE_MANIFEST_SHA,
    WITNESS_RELPATH: WITNESS_SHA,
    SNAPSHOT388_RELPATH: SNAPSHOT388_SHA,
    C13_RECEIPT_RELPATH: C13_RECEIPT_SHA,
    C16_RECON_RELPATH: C16_RECON_SHA,
    C16_INVENTORY_RELPATH: C16_INVENTORY_SHA,
    C16_SOURCE_RELPATH: C16_SOURCE_SHA,
    C17_INVENTORY_RELPATH: C17_INVENTORY_SHA,
    C17_SOURCE_RELPATH: C17_SOURCE_SHA,
}


def _resolve_selector(source_bytes: bytes, selector: str) -> None:
    parts = selector.split("::")
    if len(parts) != 3:
        raise AssertionError(f"Malformed selector: {selector}")
    _, cls_name, method_name = parts
    tree = ast.parse(source_bytes.decode("utf-8"))
    cls_node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name),
        None,
    )
    if cls_node is None:
        raise AssertionError(f"Class {cls_name} not found in AST")
    method_node = next(
        (
            n
            for n in cls_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == method_name
        ),
        None,
    )
    if method_node is None:
        raise AssertionError(f"Method {method_name} not found in class {cls_name}")


class SharedReadUserCanonicalPromotionReceiptTests(unittest.TestCase):
    def test_receipt_binds_accepted_promotion_and_preserves_open_gates(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent

        # Freeze relevant existing evidence bytes before reading target receipt
        frozen_evidence: dict[str, bytes] = {}
        for relpath, expected_sha in EVIDENCE_PINS.items():
            fpath = repo_root / relpath
            self.assertTrue(fpath.is_file(), f"Missing required evidence file: {fpath}")
            raw_bytes = fpath.read_bytes()
            actual_sha = hashlib.sha256(raw_bytes).hexdigest()
            self.assertEqual(
                actual_sha,
                expected_sha,
                f"Evidence SHA mismatch for {relpath}: expected {expected_sha}, got {actual_sha}",
            )
            frozen_evidence[relpath] = raw_bytes

        # Read and decode target receipt
        receipt_path = repo_root / TARGET_RECEIPT_RELPATH
        self.assertTrue(receipt_path.is_file(), f"Target receipt missing: {receipt_path}")
        receipt_bytes = receipt_path.read_bytes()
        receipt_doc = json.loads(receipt_bytes.decode("utf-8"))

        # Assert top-level metadata and typed fields
        self.assertIs(type(receipt_doc["schema_version"]), int)
        self.assertEqual(receipt_doc["schema_version"], 1)
        self.assertIs(type(receipt_doc["stage"]), str)
        self.assertEqual(receipt_doc["stage"], EXPECTED_STAGE)
        self.assertEqual(receipt_doc["assignment_id"], EXPECTED_ASSIGNMENT)
        self.assertEqual(receipt_doc["inspected_head"], EXPECTED_HEAD)
        self.assertEqual(receipt_doc["promotion_commit"], EXPECTED_HEAD)
        self.assertEqual(receipt_doc["status"], "PARTIAL_CANONICAL_INTEGRATION")
        self.assertIs(receipt_doc["canonical_ledger_integration_complete"], False)

        # Assert live manifest binding matches 389 live manifest
        live_manifest = receipt_doc["live_manifest"]
        self.assertEqual(live_manifest["path"], LIVE_MANIFEST_RELPATH)
        self.assertEqual(live_manifest["sha256"], LIVE_MANIFEST_SHA)
        self.assertEqual(live_manifest["accepted_commit"], EXPECTED_HEAD)

        # Assert promotion witness binding and selector resolution
        witness = receipt_doc["promotion_witness"]
        self.assertEqual(witness["path"], WITNESS_RELPATH)
        self.assertEqual(witness["sha256"], WITNESS_SHA)
        self.assertEqual(witness["accepted_commit"], EXPECTED_HEAD)
        self.assertEqual(witness["test_selector"], WITNESS_SELECTOR)
        _resolve_selector(frozen_evidence[WITNESS_RELPATH], WITNESS_SELECTOR)

        # Assert linked frozen 388 snapshot
        snap388 = receipt_doc["frozen_388_snapshot"]
        self.assertEqual(snap388["path"], SNAPSHOT388_RELPATH)
        self.assertEqual(snap388["sha256"], SNAPSHOT388_SHA)

        # Assert linked prior C13 receipt (historical 388 manifest binding)
        c13_receipt = receipt_doc["prior_c13_canonical_promotion"]
        self.assertEqual(c13_receipt["path"], C13_RECEIPT_RELPATH)
        self.assertEqual(c13_receipt["sha256"], C13_RECEIPT_SHA)
        c13_doc = json.loads(frozen_evidence[C13_RECEIPT_RELPATH].decode("utf-8"))
        self.assertEqual(c13_doc["live_manifest"]["sha256"], SNAPSHOT388_SHA)
        self.assertNotEqual(c13_doc["live_manifest"]["sha256"], LIVE_MANIFEST_SHA)

        # Assert historical accounting receipt (C16 from C15)
        hist_recon = receipt_doc["historical_accounting_receipt"]
        self.assertEqual(hist_recon["path"], C16_RECON_RELPATH)
        self.assertEqual(hist_recon["sha256"], C16_RECON_SHA)

        # Assert bound immutable source inventories
        src_invs = receipt_doc["source_inventories"]
        self.assertEqual(src_invs["continuation_16"]["path"], C16_INVENTORY_RELPATH)
        self.assertEqual(src_invs["continuation_16"]["sha256"], C16_INVENTORY_SHA)
        self.assertEqual(src_invs["continuation_17"]["path"], C17_INVENTORY_RELPATH)
        self.assertEqual(src_invs["continuation_17"]["sha256"], C17_INVENTORY_SHA)

        # Assert exact record metadata and AST selector resolution
        records = receipt_doc["records"]
        self.assertEqual(len(records), 2)
        rec_c16, rec_c17 = records[0], records[1]

        self.assertEqual(rec_c16["path"], C16_SOURCE_RELPATH)
        self.assertEqual(rec_c16["sha256"], C16_SOURCE_SHA)
        self.assertEqual(rec_c16["last_commit"], "05fe0d5c2a5dae727aa7219549843b1ce6235945")
        self.assertEqual(rec_c16["seed_ids"], [SHARED_SEED_ID])
        self.assertEqual(
            rec_c16["test_selectors"],
            [
                f"{C16_SOURCE_RELPATH}::UsersReadManagerPeerHttpCharacterizationTests::test_middle_manager_peer_read_returns_http_403_before_relation_loading"
            ],
        )
        _resolve_selector(frozen_evidence[C16_SOURCE_RELPATH], rec_c16["test_selectors"][0])

        self.assertEqual(rec_c17["path"], C17_SOURCE_RELPATH)
        self.assertEqual(rec_c17["sha256"], C17_SOURCE_SHA)
        self.assertEqual(rec_c17["last_commit"], "0f3deb613251c7c5f39822b2a1f0e5532e5e140f")
        self.assertEqual(rec_c17["seed_ids"], [SHARED_SEED_ID])
        self.assertEqual(
            rec_c17["test_selectors"],
            [
                f"{C17_SOURCE_RELPATH}::UsersReadMissingHttpCharacterizationTests::test_authorized_missing_target_returns_http_404_after_single_get"
            ],
        )
        _resolve_selector(frozen_evidence[C17_SOURCE_RELPATH], rec_c17["test_selectors"][0])

        # Validate singleton seed sets: 1 shared, 0 extra C16 contribution
        c16_seeds = set(rec_c16["seed_ids"])
        c17_seeds = set(rec_c17["seed_ids"])
        self.assertEqual(c16_seeds, {SHARED_SEED_ID})
        self.assertEqual(c17_seeds, {SHARED_SEED_ID})
        self.assertEqual(c16_seeds & c17_seeds, {SHARED_SEED_ID})
        self.assertEqual(c16_seeds - c17_seeds, set())
        self.assertEqual(receipt_doc["promoted_seed_ids"], [SHARED_SEED_ID])
        self.assertEqual(receipt_doc["already_promoted_seed"], SHARED_SEED_ID)
        self.assertEqual(receipt_doc["delta_distinct_seed_count"], 1)
        self.assertEqual(receipt_doc["c16_additional_distinct_contribution_after_c17"], 0)

        # Assert typed integer counts
        counts = receipt_doc["counts"]
        for key in (
            "expected_seed_count",
            "baseline_evidenced_seed_count",
            "reconciled_new_seed_count",
            "evidenced_seed_count",
            "remaining_seed_count",
            "historical_frozen388_evidenced_seed_count",
            "historical_frozen388_remaining_seed_count",
        ):
            self.assertIs(type(counts[key]), int)
        self.assertEqual(counts["expected_seed_count"], 465)
        self.assertEqual(counts["baseline_evidenced_seed_count"], 135)
        self.assertEqual(counts["reconciled_new_seed_count"], 254)
        self.assertEqual(counts["evidenced_seed_count"], 389)
        self.assertEqual(counts["remaining_seed_count"], 76)
        self.assertEqual(counts["historical_frozen388_evidenced_seed_count"], 388)
        self.assertEqual(counts["historical_frozen388_remaining_seed_count"], 77)

        # Assert reconciliation counters and status
        self.assertIs(type(receipt_doc["reconciled_behavior_units"]), int)
        self.assertEqual(receipt_doc["reconciled_behavior_units"], 5)
        self.assertIs(type(receipt_doc["remaining_behavior_units"]), int)
        self.assertEqual(receipt_doc["remaining_behavior_units"], 0)
        self.assertIs(type(receipt_doc["total_behavior_units"]), int)
        self.assertEqual(receipt_doc["total_behavior_units"], 5)
        self.assertIs(type(receipt_doc["pending_five"]), int)
        self.assertEqual(receipt_doc["pending_five"], 5)
        self.assertIs(type(receipt_doc["units_since_reconciliation"]), int)
        self.assertEqual(receipt_doc["units_since_reconciliation"], 5)
        self.assertIs(receipt_doc["global_reconciliation_complete"], False)

        # Assert retained open gates and blockers
        self.assertEqual(receipt_doc["coverage_status"], "PARTIAL")
        self.assertEqual(receipt_doc["seed_acceptance_status"], "FULL_SEED_ACCEPTANCE_NOT_ASSESSED")
        self.assertEqual(
            receipt_doc["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )
        self.assertEqual(receipt_doc["stages"]["P1-00"], "COMPLETE")
        self.assertEqual(receipt_doc["stages"]["P1-00b"], "IN_PROGRESS")
        self.assertEqual(receipt_doc["origin_matrix_unresolved_cells"], 24)
        self.assertIs(receipt_doc["closures_authorized"], False)

        # Assert deferrals and exclusions presence
        self.assertIn("defer_prior_unit=normal169-c16-c17-union-p確", receipt_doc["deferrals"])
        self.assertIn("canonical ledger integration/global reconciliation closeout", receipt_doc["exclusions"])

        # Confirm all frozen evidence bytes unchanged
        for relpath, original_bytes in frozen_evidence.items():
            current_bytes = (repo_root / relpath).read_bytes()
            self.assertEqual(current_bytes, original_bytes, f"Evidence byte drift detected for {relpath}")
