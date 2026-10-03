"""Hermetic single-consumer characterization witness for C17 snapshot reader isolation.

Stage: P1-00b (IN_PROGRESS)
Stage P1-00: COMPLETE
Assignment: WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169
Canonical gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work

Preserved deferrals:
defer_prior_unit=normal169-c17-consumer-loader-integration-20261003-c007;
defer_prior_unit=normal169-c17-historical-coverage-reader-isolation-20261003-c005;
defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004;
defer_prior_unit=normal169-c16-receipt-custody-20261003-c001;
defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2.

Accounting status:
5/5 evidence reconciled with canonical integration pending.
pending_five=5, units_since_reconciliation=5, global_reconciliation_complete=false.
canonical coverage 387/465, 78 remaining seeds, 24 unresolved origins.
Both High blockers open:
- P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
Stages: P1-00 COMPLETE and P1-00b IN_PROGRESS.

Exclusions:
All other C17 consumers, C13/shared C16-C17 canonical integration,
new product behavior, seed promotion, sixth behavior,
read_user self-success/super-admin rejection and authorization policy acceptance,
mutation/persistence/retry/reload, origin/seed/blocker/Stage closure.
Keep all historic receipts/ledger/live manifest/snapshot and all already-accepted evidence files untouched.

Pure stdlib unittest with JSON/SHA/AST only: no subprocess, network, application
imports, or custom Session mocks.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tests import test_p1_00b_characterization_c17_partial_reconciliation as c17_mod
from tests.test_p1_00b_characterization_seed_handler_coverage import (
    HISTORICAL_SNAPSHOT_REL_PATH,
    HISTORICAL_SNAPSHOT_SHA256,
)

C17_RECEIPT_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json"
)
CHECKPOINT_13_14_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json"
)
C17_INVENTORY_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json"
)
C16_INVENTORY_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json"
)
C17_RECORD_RELPATH = (
    "tests/test_p1_00b_characterization_users_read_missing_http.py"
)
SNAPSHOT_387_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-387-snapshot.json"
)
LIVE_COVERAGE_RELPATH = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)

SEVEN_PATH_SHA256_PINS: dict[str, str] = {
    C17_RECEIPT_RELPATH: "66c4ff5427b2e831dc386c663151113e52e1c40fda845b741a1eae84cf5f1295",
    CHECKPOINT_13_14_RELPATH: "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d",
    C17_INVENTORY_RELPATH: "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15",
    C16_INVENTORY_RELPATH: "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a",
    C17_RECORD_RELPATH: "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb",
    SNAPSHOT_387_RELPATH: "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7",
    LIVE_COVERAGE_RELPATH: "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7",
}


class C17SingleConsumerSnapshotTests(unittest.TestCase):
    """Characterization tests verifying C17 coverage reader migration and call-site isolation."""

    def test_exact_consumer_ignores_malformed_live_and_rejects_snapshot_drift(self) -> None:
        """Verify exact consumer ignores corrupted live manifest and rejects drifted snapshot."""
        canonical_root = c17_mod.REPO_ROOT
        self.assertTrue(
            canonical_root.is_dir(),
            f"Expected canonical REPO_ROOT to be directory: {canonical_root}",
        )

        # Freeze complete canonical bytes with independent literal SHA256 pins for seven-path closure
        frozen_canonical_bytes: dict[str, bytes] = {}
        for rel_path, expected_sha in SEVEN_PATH_SHA256_PINS.items():
            canon_file = canonical_root / rel_path
            self.assertTrue(
                canon_file.is_file(),
                f"Missing canonical closure file at {canon_file}",
            )
            raw = canon_file.read_bytes()
            actual_sha = hashlib.sha256(raw).hexdigest()
            self.assertEqual(
                actual_sha,
                expected_sha,
                f"Canonical SHA mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}",
            )
            frozen_canonical_bytes[rel_path] = raw

        def verify_closures(
            temp_tree_root: Path,
            expected_temp_map: dict[str, bytes],
        ) -> None:
            for p, exp_bytes in expected_temp_map.items():
                t_file = temp_tree_root / p
                self.assertTrue(t_file.is_file(), f"Temporary file missing: {t_file}")
                self.assertEqual(
                    t_file.read_bytes(),
                    exp_bytes,
                    f"Temporary closure byte mismatch for {p}",
                )
            for p, exp_bytes in frozen_canonical_bytes.items():
                c_file = canonical_root / p
                self.assertTrue(c_file.is_file(), f"Canonical file missing: {c_file}")
                self.assertEqual(
                    c_file.read_bytes(),
                    exp_bytes,
                    f"Canonical closure drift detected for {p}",
                )

        temp_dir = tempfile.mkdtemp(prefix="p1_00b_c17_single_consumer_")
        temp_root = Path(temp_dir)
        c17_consumer = c17_mod.C17PartialReconciliationTests("test_exact_receipt_and_evidence")

        try:
            # Populate temporary repository tree with complete frozen closure
            for rel_path, raw in frozen_canonical_bytes.items():
                dest = temp_root / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)

            expected_temp_bytes = dict(frozen_canonical_bytes)
            verify_closures(temp_root, expected_temp_bytes)

            # Target Call 1: Exact real consumer with intact temporary closure
            with patch.object(c17_mod, "REPO_ROOT", temp_root):
                self.assertEqual(c17_mod.REPO_ROOT, temp_root)
                c17_consumer.test_exact_receipt_and_evidence()
            self.assertEqual(c17_mod.REPO_ROOT, canonical_root)
            verify_closures(temp_root, expected_temp_bytes)

            # Target Call 2: Write literal invalid-JSON live manifest; consumer succeeds
            malformed_live = b"{\n  \"invalid_json\": true,\n  corrupted trailing syntax\n"
            (temp_root / LIVE_COVERAGE_RELPATH).write_bytes(malformed_live)
            expected_temp_bytes[LIVE_COVERAGE_RELPATH] = malformed_live

            with patch.object(c17_mod, "REPO_ROOT", temp_root):
                self.assertEqual(c17_mod.REPO_ROOT, temp_root)
                c17_consumer.test_exact_receipt_and_evidence()
            self.assertEqual(c17_mod.REPO_ROOT, canonical_root)
            verify_closures(temp_root, expected_temp_bytes)

            # Target Call 3: Retain malformed live, append newline to temporary snapshot (drift)
            drifted_snapshot = frozen_canonical_bytes[SNAPSHOT_387_RELPATH] + b"\n"
            (temp_root / SNAPSHOT_387_RELPATH).write_bytes(drifted_snapshot)
            expected_temp_bytes[SNAPSHOT_387_RELPATH] = drifted_snapshot

            with patch.object(c17_mod, "REPO_ROOT", temp_root):
                self.assertEqual(c17_mod.REPO_ROOT, temp_root)
                with self.assertRaises(ValueError) as cm:
                    c17_consumer.test_exact_receipt_and_evidence()
            self.assertEqual(c17_mod.REPO_ROOT, canonical_root)

            drift_message = str(cm.exception)
            expected_snapshot_path = str(temp_root / SNAPSHOT_387_RELPATH)
            self.assertIn("Historical snapshot byte drift detected at", drift_message)
            self.assertIn(expected_snapshot_path, drift_message)
            self.assertIn(f"expected SHA256 {HISTORICAL_SNAPSHOT_SHA256}", drift_message)

            # Immediately after rejection, compare every temporary file and canonical closure
            verify_closures(temp_root, expected_temp_bytes)

            # Target Call 4: Restore both temporary live and snapshot bytes to frozen mapping
            (temp_root / LIVE_COVERAGE_RELPATH).write_bytes(
                frozen_canonical_bytes[LIVE_COVERAGE_RELPATH]
            )
            (temp_root / SNAPSHOT_387_RELPATH).write_bytes(
                frozen_canonical_bytes[SNAPSHOT_387_RELPATH]
            )
            expected_temp_bytes[LIVE_COVERAGE_RELPATH] = frozen_canonical_bytes[LIVE_COVERAGE_RELPATH]
            expected_temp_bytes[SNAPSHOT_387_RELPATH] = frozen_canonical_bytes[SNAPSHOT_387_RELPATH]

            # Compare all temporary and canonical bytes BEFORE rerunning
            verify_closures(temp_root, expected_temp_bytes)

            with patch.object(c17_mod, "REPO_ROOT", temp_root):
                self.assertEqual(c17_mod.REPO_ROOT, temp_root)
                c17_consumer.test_exact_receipt_and_evidence()
            self.assertEqual(c17_mod.REPO_ROOT, canonical_root)

            # Immediately compare all bytes AFTER restored positive call
            verify_closures(temp_root, expected_temp_bytes)

            # Prove patch scope restores original REPO_ROOT
            self.assertEqual(c17_mod.REPO_ROOT, canonical_root)
            self.assertIs(c17_mod.REPO_ROOT, canonical_root)

            # Remove temporary tree and assert absent
            shutil.rmtree(temp_root)
            self.assertFalse(temp_root.exists(), f"Expected {temp_root} to be removed")

            # Compare complete canonical closure after cleanup
            for p, exp_bytes in frozen_canonical_bytes.items():
                c_file = canonical_root / p
                self.assertTrue(c_file.is_file(), f"Canonical file missing: {c_file}")
                self.assertEqual(
                    c_file.read_bytes(),
                    exp_bytes,
                    f"Canonical closure drift after cleanup for {p}",
                )

        finally:
            if temp_root.exists():
                try:
                    (temp_root / LIVE_COVERAGE_RELPATH).write_bytes(
                        frozen_canonical_bytes[LIVE_COVERAGE_RELPATH]
                    )
                    (temp_root / SNAPSHOT_387_RELPATH).write_bytes(
                        frozen_canonical_bytes[SNAPSHOT_387_RELPATH]
                    )
                finally:
                    shutil.rmtree(temp_root, ignore_errors=True)
            self.assertEqual(c17_mod.REPO_ROOT, canonical_root)


if __name__ == "__main__":
    unittest.main()
