from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    assert_closure_bytes,
)
from tests.test_p1_00b_characterization_c17_temporary_canary_custody import (
    CANONICAL_PINS,
    LIVE_CANARY_RELPATH,
)

EXPECTED_SNAPSHOT388_SHA: str = (
    "da0fd31c0dbc4776b548a8b235f8479fe8e60687ff51e0cb2623c3c21d4c42d7"
)

EXPECTED_FROZEN388_MANIFEST: dict[str, Any] = {
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


def read_temporary_manifest_with_custody(
    temp_root: Path,
    canonical_root: Path,
    expected_temporary_bytes: dict[str, bytes],
    expected_canonical_bytes: dict[str, bytes],
    expected_manifest: dict[str, Any],
) -> dict[str, Any]:
    """
    Read and decode temporary live manifest while strictly enforcing closure byte custody.

    Verifies closure bytes on both temporary and canonical roots before reading,
    verifies decoded JSON matches expected_manifest, and re-verifies closure bytes
    on both trees before returning.
    """
    assert_closure_bytes(temp_root, expected_temporary_bytes)
    assert_closure_bytes(canonical_root, expected_canonical_bytes)

    target_file = temp_root / LIVE_CANARY_RELPATH
    raw_bytes = target_file.read_bytes()
    decoded = json.loads(raw_bytes.decode("utf-8"))

    if decoded != expected_manifest:
        raise AssertionError("Decoded manifest does not match expected_manifest")

    assert_closure_bytes(temp_root, expected_temporary_bytes)
    assert_closure_bytes(canonical_root, expected_canonical_bytes)

    return decoded


class Frozen388LiveManifestCustodyTests(unittest.TestCase):
    """
    Test suite for frozen 388 temporary live-manifest custody adapter.

    Scope documentation:
    Evidence reconciled 5/5 but canonical shared C16/C17 promotion/accounting remains unmet:
    union one prospective seed, C16 adds zero after C17. Keep pending_five=5,
    units_since_reconciliation=5, global_reconciliation_complete=false; canonical
    coverage 388/465 (135 baseline+253 new), 77 remaining; both High blockers and 24
    origins open. read_user self-success/super-admin rejection remain open. No sixth
    behavior or seed/Stage acceptance. This unit is auxiliary custody validation only,
    not acceptance of actual product behavior. Not a handler batch: no application
    handler executed; pure stdlib temporary JSON/byte custody with one lifecycle.
    Exclusions: shared read_user promotion, canonical ledger integration, authorization
    policy, persistence/retry/reload, origin/seed/blocker/Stage closure, all other
    consumers, production/network/secrets.

    Preserve these exact deferrals in the new module scope documentation:
    defer_prior_unit=normal169-shared-read-user-manifest-only-frozen388-20261003-c004;
    defer_prior_unit=normal169-c17-only-canonical-promotion-20261003-c016-bounded;
    defer_prior_unit=normal169-c16-c17-union-promotion-with-custody-20261003-c014;
    defer_prior_unit=normal169-c16-c17-canonical-promotion-20261003-c012;
    defer_prior_unit=normal169-c16-c17-canonical-promotion-20261003-c012-v2;
    defer_prior_unit=normal169-canonical-manifest-successor-20261003‑;
    defer_prior_unit=normal169-c17-consumer-loader-integration-20261003-c007;
    defer_prior_unit=normal169-c17-historical-coverage-reader-isolation-20261003-c005;
    defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004;
    defer_prior_unit=normal169-c16-receipt-custody-20261003-c001;
    defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2.
    """

    def test_temporary_live_manifest_drift_rejected_and_restored_oracle_guarded(self) -> None:
        repo_root = Path(__file__).resolve().parent.parent

        canonical_bytes: dict[str, bytes] = {}
        for relpath in CANONICAL_PINS:
            file_path = repo_root / relpath
            self.assertTrue(file_path.is_file(), f"Missing canonical file: {file_path}")
            canonical_bytes[relpath] = file_path.read_bytes()

        self.assertEqual(len(canonical_bytes), 6)
        self.assertEqual(set(canonical_bytes.keys()), set(CANONICAL_PINS.keys()))

        for relpath, expected_sha in CANONICAL_PINS.items():
            actual_sha = hashlib.sha256(canonical_bytes[relpath]).hexdigest()
            self.assertEqual(
                actual_sha,
                expected_sha,
                f"SHA256 mismatch for canonical path {relpath}: expected {expected_sha}, got {actual_sha}",
            )

        snapshot_relpath = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-388-snapshot.json"
        )
        self.assertEqual(
            CANONICAL_PINS[snapshot_relpath],
            EXPECTED_SNAPSHOT388_SHA,
        )
        self.assertEqual(
            hashlib.sha256(canonical_bytes[snapshot_relpath]).hexdigest(),
            EXPECTED_SNAPSHOT388_SHA,
        )

        live_file_path = repo_root / LIVE_CANARY_RELPATH
        self.assertTrue(live_file_path.is_file(), f"Missing canonical live manifest: {live_file_path}")
        live_canonical_bytes = live_file_path.read_bytes()

        frozen_canonical_mapping: dict[str, bytes] = dict(canonical_bytes)
        frozen_canonical_mapping[LIVE_CANARY_RELPATH] = live_canonical_bytes
        self.assertEqual(len(frozen_canonical_mapping), 7)
        self.assertEqual(
            set(frozen_canonical_mapping.keys()),
            set(CANONICAL_PINS.keys()) | {LIVE_CANARY_RELPATH},
        )

        frozen_temporary_mapping: dict[str, bytes] = dict(frozen_canonical_mapping)

        snapshot_decoded = json.loads(canonical_bytes[snapshot_relpath].decode("utf-8"))
        live_decoded = json.loads(live_canonical_bytes.decode("utf-8"))
        self.assertEqual(snapshot_decoded, EXPECTED_FROZEN388_MANIFEST)
        self.assertEqual(live_decoded, EXPECTED_FROZEN388_MANIFEST)

        temp_dir = tempfile.TemporaryDirectory()
        try:
            temp_root = Path(temp_dir.name)
            for relpath, raw_bytes in frozen_canonical_mapping.items():
                dest_path = temp_root / relpath
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(raw_bytes)

            # R2: Initial adapter call succeeds and returns the complete expected document
            result = read_temporary_manifest_with_custody(
                temp_root=temp_root,
                canonical_root=repo_root,
                expected_temporary_bytes=frozen_temporary_mapping,
                expected_canonical_bytes=frozen_canonical_mapping,
                expected_manifest=EXPECTED_FROZEN388_MANIFEST,
            )
            self.assertEqual(result, EXPECTED_FROZEN388_MANIFEST)
            self.assertEqual(result["counts"]["baseline_evidenced_seed_count"], 135)
            self.assertEqual(result["counts"]["reconciled_new_seed_count"], 253)
            self.assertEqual(result["counts"]["evidenced_seed_count"], 388)
            self.assertEqual(result["counts"]["remaining_seed_count"], 77)
            self.assertEqual(result["coverage_status"], "PARTIAL")
            self.assertEqual(
                result["canonical_blockers"],
                [
                    "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE",
                    "P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN",
                ],
            )

            # R3: Mutate live manifest in temporary tree by appending one newline
            temp_live_file = temp_root / LIVE_CANARY_RELPATH
            original_live_bytes = frozen_temporary_mapping[LIVE_CANARY_RELPATH]
            mutated_live_bytes = original_live_bytes + b"\n"
            temp_live_file.write_bytes(mutated_live_bytes)

            mutated_decoded = json.loads(temp_live_file.read_bytes().decode("utf-8"))
            self.assertEqual(mutated_decoded, EXPECTED_FROZEN388_MANIFEST)

            # Compare every temporary path to expected current state before calling adapter
            for relpath, orig_bytes in frozen_temporary_mapping.items():
                actual = (temp_root / relpath).read_bytes()
                if relpath == LIVE_CANARY_RELPATH:
                    self.assertEqual(actual, mutated_live_bytes)
                else:
                    self.assertEqual(actual, orig_bytes)

            for relpath, orig_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), orig_bytes)

            with self.assertRaises(AssertionError) as ctx:
                read_temporary_manifest_with_custody(
                    temp_root=temp_root,
                    canonical_root=repo_root,
                    expected_temporary_bytes=frozen_temporary_mapping,
                    expected_canonical_bytes=frozen_canonical_mapping,
                    expected_manifest=EXPECTED_FROZEN388_MANIFEST,
                )

            err_msg = str(ctx.exception)
            self.assertIn("Receipt byte drift detected for", err_msg)
            self.assertIn(str(temp_live_file), err_msg)
            self.assertIn(
                f"expected {len(original_live_bytes)} bytes, found {len(mutated_live_bytes)} bytes",
                err_msg,
            )

            # Immediately after rejection, compare all temporary and canonical bytes
            for relpath, orig_bytes in frozen_temporary_mapping.items():
                actual = (temp_root / relpath).read_bytes()
                if relpath == LIVE_CANARY_RELPATH:
                    self.assertEqual(actual, mutated_live_bytes)
                else:
                    self.assertEqual(actual, orig_bytes)

            for relpath in CANONICAL_PINS:
                self.assertEqual((temp_root / relpath).read_bytes(), frozen_temporary_mapping[relpath])

            for relpath, orig_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), orig_bytes)

            # R4: Restore exact original temporary live bytes
            temp_live_file.write_bytes(original_live_bytes)

            # BEFORE calling restored adapter, call assert_closure_bytes on BOTH complete original mappings
            assert_closure_bytes(temp_root, frozen_temporary_mapping)
            assert_closure_bytes(repo_root, frozen_canonical_mapping)

            restored_result = read_temporary_manifest_with_custody(
                temp_root=temp_root,
                canonical_root=repo_root,
                expected_temporary_bytes=frozen_temporary_mapping,
                expected_canonical_bytes=frozen_canonical_mapping,
                expected_manifest=EXPECTED_FROZEN388_MANIFEST,
            )

            # Immediately AFTER success, call assert_closure_bytes on BOTH complete original mappings
            assert_closure_bytes(temp_root, frozen_temporary_mapping)
            assert_closure_bytes(repo_root, frozen_canonical_mapping)

            self.assertEqual(restored_result, EXPECTED_FROZEN388_MANIFEST)

            temp_dir.cleanup()
            self.assertFalse(temp_root.exists())

            for relpath, expected_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), expected_bytes)
            assert_closure_bytes(repo_root, frozen_canonical_mapping)
        finally:
            if Path(temp_dir.name).exists():
                temp_dir.cleanup()
