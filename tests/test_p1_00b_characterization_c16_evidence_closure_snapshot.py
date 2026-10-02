"""Bounded raw-byte evidence closure snapshot witness for C16 characterization reconciliation.

Stage: P1-00b (IN_PROGRESS)
Gap Reference: docs/refactor/two-server/section-1/00-stage-ledger.md#p1-00b-execution-status
Canonical Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This auxiliary module provides a bounded, opaque raw-byte witness over the exact ten
canonical evidence closure files required before C16 reconciliation.

Exclusions:
- No application imports, HTTP dispatch, or custom Session/database fixtures.
- No decoded-record mutant construction or provenance-validator integration.
- No new reconciliation receipt or modification of canonical evidence documents.
- No sixth behavior, seed accounting advancement, or stage/origin/blocker closure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tests.test_p1_00b_characterization_receipt_byte_preservation import (
    assert_receipt_bytes_unchanged,
)

C16_EVIDENCE_CLOSURE_MANIFEST: dict[str, str] = {
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json": (
        "b7dac667be1584c50743b24d806d2b9d1c6fe50a85ec1e9936a65ed96754ffbb"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json": (
        "66c4ff5427b2e831dc386c663151113e52e1c40fda845b741a1eae84cf5f1295"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json": (
        "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json": (
        "782407b237098640fffa0ce1c011d80406ce0b1707b9b06428a5e911e0487554"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-15.json": (
        "11bc150e1d9e1e92fd317b90078a38b6e28819d7f8b8f812ad19399dc91369b5"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-16.json": (
        "22e2c7c921ee8e20a2335a1ce1f32e0224f4b1403f2597ef6a11e36d4930fe3a"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json": (
        "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15"
    ),
    "tests/test_p1_00b_characterization_users_read_all_empty_http.py": (
        "83151fbafaa7953fbad50136ad564a0bdd2f5d39c1e5943c75a40b3364cf7faa"
    ),
    "tests/test_p1_00b_characterization_users_read_manager_peer_http.py": (
        "f093210c5dadda895a370ec1427607c3eade7e593e3133d3551881f2e8e20bcd"
    ),
    "tests/test_p1_00b_characterization_users_read_missing_http.py": (
        "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb"
    ),
}


def freeze_c16_evidence_closure(repo_root: Path) -> dict[str, bytes]:
    """Capture and verify raw bytes for exactly the ten canonical closure files."""
    repo_root = Path(repo_root)
    snapshot: dict[str, bytes] = {}
    for rel_path, expected_sha256 in C16_EVIDENCE_CLOSURE_MANIFEST.items():
        file_path = repo_root / rel_path
        if not file_path.is_file():
            raise FileNotFoundError(f"Canonical closure file not found: {file_path}")
        raw_bytes = file_path.read_bytes()
        actual_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        if actual_sha256 != expected_sha256:
            raise AssertionError(
                f"SHA256 mismatch for canonical file {rel_path}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        snapshot[rel_path] = raw_bytes

    if set(snapshot.keys()) != set(C16_EVIDENCE_CLOSURE_MANIFEST.keys()):
        raise AssertionError("Snapshot keys do not match exact canonical closure keys")
    return snapshot


def assert_c16_evidence_closure_unchanged(
    evidence_root: Path,
    frozen_snapshot: dict[str, bytes],
) -> None:
    """Assert all ten closure files match the original frozen snapshot bytes."""
    evidence_root = Path(evidence_root)
    expected_keys = set(C16_EVIDENCE_CLOSURE_MANIFEST.keys())
    if set(frozen_snapshot.keys()) != expected_keys:
        raise AssertionError(
            f"frozen_snapshot keys mismatch: expected {expected_keys}, got {set(frozen_snapshot.keys())}"
        )
    for rel_path in C16_EVIDENCE_CLOSURE_MANIFEST:
        target_path = evidence_root / rel_path
        expected_bytes = frozen_snapshot[rel_path]
        assert_receipt_bytes_unchanged(target_path, expected_bytes)


class C16EvidenceClosureSnapshotTests(unittest.TestCase):
    def test_exact_ten_file_closure_snapshot(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        frozen_snapshot = freeze_c16_evidence_closure(repo_root)

        # Prove exact path set and SHA256 manifest
        self.assertEqual(
            set(frozen_snapshot.keys()),
            set(C16_EVIDENCE_CLOSURE_MANIFEST.keys()),
        )
        self.assertEqual(len(frozen_snapshot), 10)
        for rel_path, expected_sha in C16_EVIDENCE_CLOSURE_MANIFEST.items():
            self.assertEqual(
                hashlib.sha256(frozen_snapshot[rel_path]).hexdigest(),
                expected_sha,
            )

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_root = Path(temp_dir_str)
            for rel_path, content_bytes in frozen_snapshot.items():
                dest_file = temp_root / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(content_bytes)

            assert_c16_evidence_closure_unchanged(temp_root, frozen_snapshot)
            assert_c16_evidence_closure_unchanged(repo_root, frozen_snapshot)

            for rel_path in frozen_snapshot:
                self.assertEqual((temp_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual(
                    (temp_root / rel_path).read_bytes(),
                    (repo_root / rel_path).read_bytes(),
                )

        # After temporary-copy cleanup recheck every canonical raw byte
        assert_c16_evidence_closure_unchanged(repo_root, frozen_snapshot)
        for rel_path in frozen_snapshot:
            self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])

    def test_predecessor_whitespace_drift_rejected_and_restored(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        frozen_snapshot = freeze_c16_evidence_closure(repo_root)

        target_relpath = (
            "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-14.json"
        )
        original_continuation_bytes = frozen_snapshot[target_relpath]

        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_root = Path(temp_dir_str)
            for rel_path, content_bytes in frozen_snapshot.items():
                dest_file = temp_root / rel_path
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(content_bytes)

            # Prove full unchanged closure before probing
            assert_c16_evidence_closure_unchanged(temp_root, frozen_snapshot)
            assert_c16_evidence_closure_unchanged(repo_root, frozen_snapshot)
            for rel_path in frozen_snapshot:
                self.assertEqual((temp_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])

            # Change ONLY temporary continuation-14.json by appending whitespace
            temp_target_file = temp_root / target_relpath
            drifted_bytes = original_continuation_bytes + b"\n"
            temp_target_file.write_bytes(drifted_bytes)

            # Prove bytes differ yet decoded JSON is equal
            self.assertNotEqual(temp_target_file.read_bytes(), original_continuation_bytes)
            original_json = json.loads(original_continuation_bytes.decode("utf-8"))
            drifted_json = json.loads(temp_target_file.read_bytes().decode("utf-8"))
            self.assertEqual(drifted_json, original_json)

            # Prove the other nine temporary files plus ALL ten canonical files still equal the original snapshot
            for rel_path in frozen_snapshot:
                if rel_path != target_relpath:
                    self.assertEqual((temp_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])

            # Require AssertionError from the SAME ten-file closure oracle
            with self.assertRaises(AssertionError) as exc_info:
                assert_c16_evidence_closure_unchanged(temp_root, frozen_snapshot)
            err_msg = str(exc_info.exception)
            self.assertIn("recent-characterization-continuation-14.json", err_msg)
            self.assertIn("drift", err_msg.lower())

            # After rejection confirm the complete canonical closure and other-nine temporary bytes again
            assert_c16_evidence_closure_unchanged(repo_root, frozen_snapshot)
            for rel_path in frozen_snapshot:
                if rel_path != target_relpath:
                    self.assertEqual((temp_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])

            # Restore continuation-14 from the ORIGINAL snapshot
            temp_target_file.write_bytes(original_continuation_bytes)

            # Rerun the SAME whole-ten-file oracle and directly compare all ten copied and canonical raw bytes
            assert_c16_evidence_closure_unchanged(temp_root, frozen_snapshot)
            assert_c16_evidence_closure_unchanged(repo_root, frozen_snapshot)
            for rel_path in frozen_snapshot:
                self.assertEqual((temp_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])
                self.assertEqual(
                    (temp_root / rel_path).read_bytes(),
                    (repo_root / rel_path).read_bytes(),
                )

        # Preserve canonical raw bytes even after cleanup
        assert_c16_evidence_closure_unchanged(repo_root, frozen_snapshot)
        for rel_path in frozen_snapshot:
            self.assertEqual((repo_root / rel_path).read_bytes(), frozen_snapshot[rel_path])


if __name__ == "__main__":
    unittest.main()
