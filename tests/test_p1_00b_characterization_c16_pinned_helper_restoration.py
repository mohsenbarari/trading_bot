from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    FROZEN_HEAD_MANIFEST,
    freeze_evidence_manifest,
)
from tests.test_p1_00b_characterization_c16_c17_shared_seed_contribution import (
    PINNED_ADDITIONAL_MANIFEST,
    assert_complete_closure_unchanged,
)
from tests.test_p1_00b_characterization_c16_evidence_closure_snapshot import (
    C16_EVIDENCE_CLOSURE_MANIFEST,
    assert_c16_evidence_closure_unchanged,
    freeze_c16_evidence_closure,
)
from tests.test_p1_00b_characterization_receipt_byte_preservation import (
    assert_receipt_bytes_unchanged,
)

# Scope and Exclusions Documentation:
# Assignment: WA-P1-00B-GATE-FIRST-RECONCILIATION-20261002-169
# Tracked stage ledger gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
# Blockers: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN
# Stages: P1-00 COMPLETE, P1-00b IN_PROGRESS
# Accounting: 387/465 evidenced seeds, 78 remaining seeds, 24 unresolved origin cells
# Reconciliation state: 4/5 reconciled units, 1 remaining unit (manager-peer HTTP403)
# Auxiliary witness scope: byte preservation drift/restoration only. No progress claimed.
# Exclusions: read_user self-success, super-admin rejection, authorization policy coverage,
# mutation, persistence, retry, reload, origin matrix cells, seed-blocker closure, Stage closure.

SHARED_HELPER_RELPATH = (
    "tests/test_p1_00b_characterization_c16_c17_shared_seed_contribution.py"
)
SHARED_HELPER_SHA256 = (
    "b190f6f762a38b6232743b0ee0949cac4bd2a85bca888f5fb45ee153b669aa20"
)
RECEIPT_HELPER_RELPATH = (
    "tests/test_p1_00b_characterization_receipt_byte_preservation.py"
)

PINNED_HELPER_MANIFEST: dict[str, str] = {
    **PINNED_ADDITIONAL_MANIFEST,
    SHARED_HELPER_RELPATH: SHARED_HELPER_SHA256,
}


def freeze_pinned_helper_manifest(repo_root: Path) -> dict[str, bytes]:
    """Capture and verify raw bytes for all pinned helper sources."""
    repo_root = Path(repo_root)
    snapshot: dict[str, bytes] = {}
    for rel_path, expected_sha in sorted(PINNED_HELPER_MANIFEST.items()):
        file_path = repo_root / rel_path
        if not file_path.is_file():
            raise AssertionError(f"Pinned helper file missing: {file_path}")
        raw_bytes = file_path.read_bytes()
        actual_sha = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha != expected_sha:
            raise AssertionError(
                f"Pinned helper SHA mismatch for {rel_path}: expected {expected_sha}, got {actual_sha}"
            )
        snapshot[rel_path] = raw_bytes

    if set(snapshot.keys()) != set(PINNED_HELPER_MANIFEST.keys()):
        raise AssertionError("Snapshot keys do not match exact pinned helper keys")
    return snapshot


def merge_complete_snapshot(*snapshots: dict[str, bytes]) -> dict[str, bytes]:
    """Merge snapshots, requiring identical bytes for overlapping keys with no silent overwrite."""
    merged: dict[str, bytes] = {}
    for snap in snapshots:
        for rel_path, raw_bytes in snap.items():
            if rel_path in merged:
                if merged[rel_path] != raw_bytes:
                    raise AssertionError(
                        f"Overlapping path {rel_path} has conflicting bytes across snapshots"
                    )
            else:
                merged[rel_path] = raw_bytes
    return merged


class C16PinnedHelperRestorationTests(unittest.TestCase):
    """Auxiliary characterization witness for C16 pinned helper drift rejection and restoration."""

    def test_pinned_helper_source_drift_rejected_and_complete_closure_restored(
        self,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        original_canonical_snapshot = freeze_evidence_manifest(repo_root)
        original_ten_file_snapshot = freeze_c16_evidence_closure(repo_root)
        original_helper_snapshot = freeze_pinned_helper_manifest(repo_root)

        self.assertEqual(
            set(original_canonical_snapshot.keys()),
            set(FROZEN_HEAD_MANIFEST.keys()),
        )
        for rel_path, expected_sha in FROZEN_HEAD_MANIFEST.items():
            self.assertEqual(
                hashlib.sha256(original_canonical_snapshot[rel_path]).hexdigest(),
                expected_sha,
            )

        self.assertEqual(
            set(original_ten_file_snapshot.keys()),
            set(C16_EVIDENCE_CLOSURE_MANIFEST.keys()),
        )
        for rel_path, expected_sha in C16_EVIDENCE_CLOSURE_MANIFEST.items():
            self.assertEqual(
                hashlib.sha256(original_ten_file_snapshot[rel_path]).hexdigest(),
                expected_sha,
            )

        self.assertEqual(
            set(original_helper_snapshot.keys()),
            set(PINNED_HELPER_MANIFEST.keys()),
        )
        for rel_path, expected_sha in PINNED_HELPER_MANIFEST.items():
            self.assertEqual(
                hashlib.sha256(original_helper_snapshot[rel_path]).hexdigest(),
                expected_sha,
            )

        original_complete_snapshot = merge_complete_snapshot(
            original_canonical_snapshot,
            original_ten_file_snapshot,
            original_helper_snapshot,
        )
        expected_complete_keys = (
            set(original_canonical_snapshot.keys())
            | set(original_ten_file_snapshot.keys())
            | set(original_helper_snapshot.keys())
        )
        self.assertEqual(set(original_complete_snapshot.keys()), expected_complete_keys)

        overlap_canonical_ten = (
            set(original_canonical_snapshot.keys())
            & set(original_ten_file_snapshot.keys())
        )
        self.assertTrue(len(overlap_canonical_ten) > 0)
        for rel_path in overlap_canonical_ten:
            self.assertEqual(
                original_canonical_snapshot[rel_path],
                original_ten_file_snapshot[rel_path],
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            for rel_path, raw_bytes in original_complete_snapshot.items():
                dest = temp_root / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw_bytes)

            assert_complete_closure_unchanged(repo_root, original_complete_snapshot)
            assert_complete_closure_unchanged(temp_root, original_complete_snapshot)

            temp_receipt_path = temp_root / RECEIPT_HELPER_RELPATH
            original_receipt_bytes = original_helper_snapshot[RECEIPT_HELPER_RELPATH]
            drifted_receipt_bytes = original_receipt_bytes + b"\n"
            temp_receipt_path.write_bytes(drifted_receipt_bytes)

            self.assertNotEqual(temp_receipt_path.read_bytes(), original_receipt_bytes)
            self.assertEqual(temp_receipt_path.read_bytes(), drifted_receipt_bytes)

            with self.assertRaises(AssertionError) as cm:
                assert_complete_closure_unchanged(temp_root, original_complete_snapshot)

            diagnostic = str(cm.exception)
            self.assertIn(RECEIPT_HELPER_RELPATH, diagnostic)
            self.assertIn("byte drift", diagnostic.lower())

            assert_complete_closure_unchanged(repo_root, original_complete_snapshot)

            for rel_path, exp_bytes in original_canonical_snapshot.items():
                if rel_path != RECEIPT_HELPER_RELPATH:
                    self.assertEqual((temp_root / rel_path).read_bytes(), exp_bytes)
            for rel_path, exp_bytes in original_ten_file_snapshot.items():
                if rel_path != RECEIPT_HELPER_RELPATH:
                    self.assertEqual((temp_root / rel_path).read_bytes(), exp_bytes)
            for rel_path, exp_bytes in original_helper_snapshot.items():
                if rel_path != RECEIPT_HELPER_RELPATH:
                    self.assertEqual((temp_root / rel_path).read_bytes(), exp_bytes)
            for rel_path, exp_bytes in original_complete_snapshot.items():
                if rel_path != RECEIPT_HELPER_RELPATH:
                    self.assertEqual((temp_root / rel_path).read_bytes(), exp_bytes)

            temp_receipt_path.write_bytes(original_receipt_bytes)
            self.assertEqual(temp_receipt_path.read_bytes(), original_receipt_bytes)

            assert_complete_closure_unchanged(temp_root, original_complete_snapshot)
            assert_complete_closure_unchanged(repo_root, original_complete_snapshot)

            assert_c16_evidence_closure_unchanged(temp_root, original_ten_file_snapshot)
            assert_c16_evidence_closure_unchanged(repo_root, original_ten_file_snapshot)

            for current_root in (temp_root, repo_root):
                for rel_path, exp_bytes in original_canonical_snapshot.items():
                    self.assertEqual((current_root / rel_path).read_bytes(), exp_bytes)
                for rel_path, exp_bytes in original_ten_file_snapshot.items():
                    self.assertEqual((current_root / rel_path).read_bytes(), exp_bytes)
                for rel_path, exp_bytes in original_helper_snapshot.items():
                    self.assertEqual((current_root / rel_path).read_bytes(), exp_bytes)

        self.assertFalse(temp_root.exists())

        assert_complete_closure_unchanged(repo_root, original_complete_snapshot)
        assert_c16_evidence_closure_unchanged(repo_root, original_ten_file_snapshot)

        for rel_path, exp_bytes in original_canonical_snapshot.items():
            self.assertEqual((repo_root / rel_path).read_bytes(), exp_bytes)
        for rel_path, exp_bytes in original_ten_file_snapshot.items():
            self.assertEqual((repo_root / rel_path).read_bytes(), exp_bytes)
        for rel_path, exp_bytes in original_helper_snapshot.items():
            self.assertEqual((repo_root / rel_path).read_bytes(), exp_bytes)


if __name__ == "__main__":
    unittest.main()
