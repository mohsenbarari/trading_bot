from __future__ import annotations

"""
Live manifest shared read_user promotion witness under P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.

Scope documentation and deferrals:
defer_prior_unit=normal169-shared-read-user-custody-promotion-receipt-20261003-c007;
defer_prior_unit=normal169-shared-read-user-manifest-only-frozen388-20261003-c004;
defer_prior_unit=normal169-c17-only-canonical-promotion-20261003-c016-bounded;
defer_prior_unit=normal169-c16-c17-union-promotion-with-custody-20261003-c014;
defer_prior_unit=normal169-c16-c17-canonical-promotion-20261003-c012;
defer_prior_unit=normal169-c16-c17-canonical-promotion-20261003-c012-v2;
defer_prior_unit=normal169-canonical-manifest-pre.

Retain accepted custody adapter commit c477daddebbd66b88d5fcaeac1c0f82f777ea310 and shared seed contribution commit ea1f081ab54bee071c56f2f59050a719b2559b41.
Auxiliary witness is not new product behavior or full seed/Stage acceptance. No source/product changes, auth-policy acceptance, read_user self-success, super-admin rejection, mutation/persistence/retry/reload, origin/seed-blocker/Stage closure, production/network/secrets.
"""

import ast
import copy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    assert_closure_bytes,
    compute_canonical_accounting,
    freeze_evidence_manifest,
    FROZEN_HEAD_MANIFEST,
)
from tests.test_p1_00b_characterization_c17_temporary_canary_custody import (
    LIVE_CANARY_RELPATH,
)
from tests.test_p1_00b_characterization_frozen388_live_manifest_custody_adapter import (
    EXPECTED_FROZEN388_MANIFEST,
    EXPECTED_SNAPSHOT388_SHA,
    read_temporary_manifest_with_custody,
)

SHARED_SEED_ID: str = "API-IDENTITY-GET-USERS-READ-USER-L440"

EXPECTED_C16_INVENTORY_PATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
)
EXPECTED_C16_INVENTORY_SHA: str = (
    "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a"
)
EXPECTED_C16_SOURCE_PATH: str = (
    "tests/test_p1_00b_characterization_users_read_manager_peer_http.py"
)
EXPECTED_C16_SOURCE_SHA: str = (
    "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd"
)

EXPECTED_C17_INVENTORY_PATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
)
EXPECTED_C17_INVENTORY_SHA: str = (
    "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15"
)
EXPECTED_C17_SOURCE_PATH: str = (
    "tests/test_p1_00b_characterization_users_read_missing_http.py"
)
EXPECTED_C17_SOURCE_SHA: str = (
    "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb"
)

RECONCILIATION_C16_FROM_C15_PATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c16-from-c15.json"
)
RECONCILIATION_C16_FROM_C15_SHA: str = (
    "956103bddc94004da7a80dd4a73f9fc4dd05fde25c7dea3d126d8da6c80d5cce"
)

EXPECTED_SNAPSHOT388_PATH: str = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-388-snapshot.json"
)


class SharedReadUserLiveSetPromotionTests(unittest.TestCase):
    """Live-set accounting witness verifying promotion of shared read_user seed."""

    def test_live_manifest_promotes_exactly_one_shared_seed_with_custody(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent

        # Freeze real historical 387 evidence and compute canonical accounting sets
        frozen_387 = freeze_evidence_manifest(repo_root)
        (
            per_file_counts,
            surface_seeds,
            baseline_seeds,
            new_beyond_baseline,
            evidenced_seeds_387,
            remaining_seeds_387,
        ) = compute_canonical_accounting(frozen_387)

        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(new_beyond_baseline), 252)
        self.assertEqual(len(evidenced_seeds_387), 387)
        self.assertEqual(len(remaining_seeds_387), 78)

        # Pin immutable 388 snapshot SHA and compare full decoded document
        snapshot388_file = repo_root / EXPECTED_SNAPSHOT388_PATH
        self.assertTrue(snapshot388_file.is_file(), f"Missing 388 snapshot: {snapshot388_file}")
        snapshot388_bytes = snapshot388_file.read_bytes()
        self.assertEqual(
            hashlib.sha256(snapshot388_bytes).hexdigest(),
            EXPECTED_SNAPSHOT388_SHA,
        )
        snapshot388_doc = json.loads(snapshot388_bytes.decode("utf-8"))
        self.assertEqual(snapshot388_doc, EXPECTED_FROZEN388_MANIFEST)

        # Freeze independent expected full 389 document before custody calls
        expected_389_manifest = copy.deepcopy(EXPECTED_FROZEN388_MANIFEST)
        expected_389_manifest["evidence_sources"]["continuation_16_path"] = EXPECTED_C16_INVENTORY_PATH
        expected_389_manifest["evidence_sources"]["continuation_17_path"] = EXPECTED_C17_INVENTORY_PATH
        expected_389_manifest["counts"] = {
            "baseline_evidenced_seed_count": 135,
            "reconciled_new_seed_count": 254,
            "evidenced_seed_count": 389,
            "remaining_seed_count": 76,
        }

        # Verify C16 singleton record metadata, pin inventory/source SHAs, resolve AST selector
        c16_inv_file = repo_root / EXPECTED_C16_INVENTORY_PATH
        self.assertTrue(c16_inv_file.is_file(), f"Missing C16 inventory: {c16_inv_file}")
        c16_inv_bytes = c16_inv_file.read_bytes()
        self.assertEqual(
            hashlib.sha256(c16_inv_bytes).hexdigest(),
            EXPECTED_C16_INVENTORY_SHA,
        )
        c16_inv = json.loads(c16_inv_bytes.decode("utf-8"))
        self.assertEqual(c16_inv["schema_version"], 1)
        self.assertEqual(c16_inv["stage"], "P1-00b")
        self.assertEqual(len(c16_inv["records"]), 1)
        c16_rec = c16_inv["records"][0]
        self.assertEqual(c16_rec["path"], EXPECTED_C16_SOURCE_PATH)
        self.assertEqual(c16_rec["sha256"], EXPECTED_C16_SOURCE_SHA)
        self.assertEqual(c16_rec["seed_ids"], [SHARED_SEED_ID])

        c16_src_file = repo_root / EXPECTED_C16_SOURCE_PATH
        self.assertTrue(c16_src_file.is_file(), f"Missing C16 source: {c16_src_file}")
        c16_src_bytes = c16_src_file.read_bytes()
        self.assertEqual(
            hashlib.sha256(c16_src_bytes).hexdigest(),
            EXPECTED_C16_SOURCE_SHA,
        )

        self.assertEqual(len(c16_rec["test_selectors"]), 1)
        c16_sel = c16_rec["test_selectors"][0]
        c16_parts = c16_sel.split("::")
        self.assertEqual(len(c16_parts), 3)
        self.assertEqual(c16_parts[0], c16_rec["path"])
        c16_tree = ast.parse(c16_src_bytes.decode("utf-8"))
        c16_cls = next(
            (n for n in c16_tree.body if isinstance(n, ast.ClassDef) and n.name == c16_parts[1]),
            None,
        )
        self.assertIsNotNone(c16_cls, f"Class {c16_parts[1]} not found in {c16_rec['path']}")
        c16_method = next(
            (
                n
                for n in c16_cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == c16_parts[2]
            ),
            None,
        )
        self.assertIsNotNone(c16_method, f"Method {c16_parts[2]} not found in {c16_parts[1]}")

        # Verify C17 singleton record metadata, pin inventory/source SHAs, resolve AST selector
        c17_inv_file = repo_root / EXPECTED_C17_INVENTORY_PATH
        self.assertTrue(c17_inv_file.is_file(), f"Missing C17 inventory: {c17_inv_file}")
        c17_inv_bytes = c17_inv_file.read_bytes()
        self.assertEqual(
            hashlib.sha256(c17_inv_bytes).hexdigest(),
            EXPECTED_C17_INVENTORY_SHA,
        )
        c17_inv = json.loads(c17_inv_bytes.decode("utf-8"))
        self.assertEqual(c17_inv["schema_version"], 1)
        self.assertEqual(c17_inv["stage"], "P1-00b")
        self.assertEqual(len(c17_inv["records"]), 1)
        c17_rec = c17_inv["records"][0]
        self.assertEqual(c17_rec["path"], EXPECTED_C17_SOURCE_PATH)
        self.assertEqual(c17_rec["sha256"], EXPECTED_C17_SOURCE_SHA)
        self.assertEqual(c17_rec["seed_ids"], [SHARED_SEED_ID])

        c17_src_file = repo_root / EXPECTED_C17_SOURCE_PATH
        self.assertTrue(c17_src_file.is_file(), f"Missing C17 source: {c17_src_file}")
        c17_src_bytes = c17_src_file.read_bytes()
        self.assertEqual(
            hashlib.sha256(c17_src_bytes).hexdigest(),
            EXPECTED_C17_SOURCE_SHA,
        )

        self.assertEqual(len(c17_rec["test_selectors"]), 1)
        c17_sel = c17_rec["test_selectors"][0]
        c17_parts = c17_sel.split("::")
        self.assertEqual(len(c17_parts), 3)
        self.assertEqual(c17_parts[0], c17_rec["path"])
        c17_tree = ast.parse(c17_src_bytes.decode("utf-8"))
        c17_cls = next(
            (n for n in c17_tree.body if isinstance(n, ast.ClassDef) and n.name == c17_parts[1]),
            None,
        )
        self.assertIsNotNone(c17_cls, f"Class {c17_parts[1]} not found in {c17_rec['path']}")
        c17_method = next(
            (
                n
                for n in c17_cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == c17_parts[2]
            ),
            None,
        )
        self.assertIsNotNone(c17_method, f"Method {c17_parts[2]} not found in {c17_parts[1]}")

        # Pin reconciliation-c16-from-c15 SHA
        recon_file = repo_root / RECONCILIATION_C16_FROM_C15_PATH
        self.assertTrue(recon_file.is_file(), f"Missing reconciliation file: {recon_file}")
        recon_bytes = recon_file.read_bytes()
        self.assertEqual(
            hashlib.sha256(recon_bytes).hexdigest(),
            RECONCILIATION_C16_FROM_C15_SHA,
        )

        # Read canonical live manifest bytes
        live_manifest_file = repo_root / LIVE_CANARY_RELPATH
        self.assertTrue(live_manifest_file.is_file(), f"Missing live manifest: {live_manifest_file}")
        live_manifest_bytes = live_manifest_file.read_bytes()

        # Build complete canonical byte mapping
        canonical_byte_mapping: dict[str, bytes] = dict(frozen_387)
        canonical_byte_mapping[EXPECTED_SNAPSHOT388_PATH] = snapshot388_bytes
        canonical_byte_mapping[EXPECTED_C16_INVENTORY_PATH] = c16_inv_bytes
        canonical_byte_mapping[EXPECTED_C16_SOURCE_PATH] = c16_src_bytes
        canonical_byte_mapping[EXPECTED_C17_INVENTORY_PATH] = c17_inv_bytes
        canonical_byte_mapping[EXPECTED_C17_SOURCE_PATH] = c17_src_bytes
        canonical_byte_mapping[RECONCILIATION_C16_FROM_C15_PATH] = recon_bytes
        canonical_byte_mapping[LIVE_CANARY_RELPATH] = live_manifest_bytes

        # Temporary tree is exact copy of canonical mapping
        temporary_byte_mapping: dict[str, bytes] = dict(canonical_byte_mapping)

        # Execute custody adapter call in isolated temporary tree
        temp_dir = tempfile.TemporaryDirectory()
        try:
            temp_root = Path(temp_dir.name)
            for relpath, raw_bytes in temporary_byte_mapping.items():
                dest = temp_root / relpath
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw_bytes)

            assert_closure_bytes(temp_root, temporary_byte_mapping)
            assert_closure_bytes(repo_root, canonical_byte_mapping)

            decoded_manifest = read_temporary_manifest_with_custody(
                temp_root=temp_root,
                canonical_root=repo_root,
                expected_temporary_bytes=temporary_byte_mapping,
                expected_canonical_bytes=canonical_byte_mapping,
                expected_manifest=expected_389_manifest,
            )
            self.assertEqual(decoded_manifest, expected_389_manifest)

            assert_closure_bytes(temp_root, temporary_byte_mapping)
            assert_closure_bytes(repo_root, canonical_byte_mapping)

            temp_dir.cleanup()
            self.assertFalse(temp_root.exists())
            assert_closure_bytes(repo_root, canonical_byte_mapping)
        finally:
            if Path(temp_dir.name).exists():
                temp_dir.cleanup()
            assert_closure_bytes(repo_root, canonical_byte_mapping)

        # Compute real 388 set independently (387 + C13)
        c13_bytes = frozen_387[
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-13.json"
        ]
        c13_doc = json.loads(c13_bytes.decode("utf-8"))
        c13_seeds = set(c13_doc["records"][0]["seed_ids"])
        computed_388_seeds = evidenced_seeds_387 | c13_seeds

        # Assert counts before promotion: 465 / 135 / 253 / 388 / 77
        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(computed_388_seeds - baseline_seeds), 253)
        self.assertEqual(len(computed_388_seeds), 388)
        self.assertEqual(len(surface_seeds - computed_388_seeds), 77)

        # Compute real 389 set independently
        computed_389_seeds = computed_388_seeds | {SHARED_SEED_ID}

        # Assert counts after promotion: 465 / 135 / 254 / 389 / 76
        self.assertEqual(len(surface_seeds), 465)
        self.assertEqual(len(baseline_seeds), 135)
        self.assertEqual(len(computed_389_seeds - baseline_seeds), 254)
        self.assertEqual(len(computed_389_seeds), 389)
        self.assertEqual(len(surface_seeds - computed_389_seeds), 76)

        # Build actual live seed union by reading EVERY evidence source named by actual live manifest
        actual_live_seeds: set[str] = set(baseline_seeds)
        live_sources = decoded_manifest["evidence_sources"]
        for src_key, src_path in live_sources.items():
            if src_key == "baseline_path":
                continue
            self.assertIn(src_path, canonical_byte_mapping, f"Missing evidence source: {src_path}")
            src_bytes = canonical_byte_mapping[src_path]
            src_doc = json.loads(src_bytes.decode("utf-8"))
            for rec in src_doc.get("records", []):
                for s_id in rec.get("seed_ids", []):
                    actual_live_seeds.add(s_id)

        # Compare full actual live set to independently computed 389 set
        self.assertEqual(actual_live_seeds, computed_389_seeds)

        # Exact set delta: only the shared seed
        self.assertEqual(computed_389_seeds - computed_388_seeds, {SHARED_SEED_ID})
        # No removed seeds
        self.assertEqual(computed_388_seeds - computed_389_seeds, set())

        # C16 and C17 singleton seed sets identical and C16 adds zero after C17
        c16_seed_set = set(c16_rec["seed_ids"])
        c17_seed_set = set(c17_rec["seed_ids"])
        self.assertEqual(c16_seed_set, {SHARED_SEED_ID})
        self.assertEqual(c17_seed_set, {SHARED_SEED_ID})
        self.assertEqual(c16_seed_set, c17_seed_set)
        self.assertEqual(
            computed_388_seeds | c17_seed_set | c16_seed_set,
            computed_388_seeds | c17_seed_set,
        )

        # Exact builtin int checks for manifest numeric fields
        self.assertIs(type(decoded_manifest["schema_version"]), int)
        self.assertEqual(decoded_manifest["schema_version"], 1)
        self.assertIs(type(decoded_manifest["inventory"]["expected_seed_count"]), int)
        self.assertEqual(decoded_manifest["inventory"]["expected_seed_count"], 465)
        counts = decoded_manifest["counts"]
        self.assertIs(type(counts["baseline_evidenced_seed_count"]), int)
        self.assertEqual(counts["baseline_evidenced_seed_count"], 135)
        self.assertIs(type(counts["reconciled_new_seed_count"]), int)
        self.assertEqual(counts["reconciled_new_seed_count"], 254)
        self.assertIs(type(counts["evidenced_seed_count"]), int)
        self.assertEqual(counts["evidenced_seed_count"], 389)
        self.assertIs(type(counts["remaining_seed_count"]), int)
        self.assertEqual(counts["remaining_seed_count"], 76)

        # Assert retained receipt counters and reconciliation status
        recon_doc = json.loads(recon_bytes.decode("utf-8"))
        self.assertEqual(recon_doc["reconciled_behavior_units"], 5)
        self.assertEqual(recon_doc["total_behavior_units"], 5)
        self.assertEqual(recon_doc["remaining_behavior_units"], 0)
        self.assertEqual(recon_doc["pending_five"], 5)
        self.assertEqual(recon_doc["units_since_reconciliation"], 5)
        self.assertIs(recon_doc["reconciliation_policy"]["global_reconciliation_complete"], False)
        self.assertEqual(recon_doc["seed_acceptance_status"], "FULL_SEED_ACCEPTANCE_NOT_ASSESSED")
        self.assertEqual(recon_doc["origin_matrix_unresolved_cells"], 24)
        self.assertIs(recon_doc["closures_authorized"], False)
        self.assertEqual(recon_doc["stages"]["P1-00"], "COMPLETE")
        self.assertEqual(recon_doc["stages"]["P1-00b"], "IN_PROGRESS")
        self.assertEqual(
            recon_doc["canonical_blockers"],
            [
                "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
            ],
        )
