from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from tests.test_p1_00b_characterization_c13_canonical_seed_membership import (
    assert_closure_bytes,
)

CANONICAL_PINS: dict[str, str] = {
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-17.json": (
        "66c4ff5427b2e831dc386c663151113e52e1c40fda845b741a1eae84cf5f1295"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-13-14.json": (
        "80e23877c849688cddb567b88898ee1440adee0a65a07237b5325d36dcab0a6d"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-continuation-17.json": (
        "f3d6f12ade5bb8933472a727ba3e880070bdca758148b2a98ce81e404c18bd15"
    ),
    "tests/test_p1_00b_characterization_users_read_missing_http.py": (
        "7ae68ea6d3239cf6ab844b3087efe45a56a38b068308316861362b1ff71c8dfb"
    ),
    "docs/refactor/two-server/section-1/inventory/recent-characterization-seed-coverage-387-snapshot.json": (
        "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7"
    ),
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json": (
        "3a2662a8e60068566582744461bb12cfc3788d3ea04857ca23f99028548f25b7"
    ),
}

LIVE_CANARY_RELPATH: str = (
    "docs/refactor/two-server/section-1/inventory/seed-handler-coverage.json"
)
LITERAL_CANARY_BYTES: bytes = (
    b'{"witness": "C17-TEMPORARY-CANARY-CUSTODY-LITERAL-V1"}'
)
MUTATED_CANARY_BYTES: bytes = LITERAL_CANARY_BYTES + b"\n"


class C17TemporaryCanaryCustodyTests(unittest.TestCase):
    """
    Test suite for C17 temporary canary custody witness.

    Preserve these deferrals verbatim in the test scope documentation:
    defer_prior_unit=normal169-c17-consumer-loader-integration-20261003-c007;
    defer_prior_unit=normal169-c17-historical-coverage-reader-isolation-20261003-c005;
    defer_prior_unit=normal169-historical-coverage-isolation-20261003-c004;
    defer_prior_unit=normal169-c16-receipt-custody-20261003-c001;
    defer_prior_unit=normal169-c16-machine-linkage-20261003-c001-v2.
    """

    def test_live_canary_mutation_rejected_and_complete_custody_restored(self) -> None:
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

        frozen_canonical_mapping: dict[str, bytes] = dict(canonical_bytes)

        temp_dir = tempfile.TemporaryDirectory()
        try:
            temp_root = Path(temp_dir.name)
            for relpath, raw_bytes in canonical_bytes.items():
                dest_path = temp_root / relpath
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(raw_bytes)

            live_file = temp_root / LIVE_CANARY_RELPATH
            live_file.write_bytes(LITERAL_CANARY_BYTES)

            frozen_temporary_mapping: dict[str, bytes] = dict(canonical_bytes)
            frozen_temporary_mapping[LIVE_CANARY_RELPATH] = LITERAL_CANARY_BYTES

            assert_closure_bytes(temp_root, frozen_temporary_mapping)
            assert_closure_bytes(repo_root, frozen_canonical_mapping)

            for relpath, expected_bytes in frozen_temporary_mapping.items():
                self.assertEqual((temp_root / relpath).read_bytes(), expected_bytes)

            for relpath, expected_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), expected_bytes)

            live_file.write_bytes(MUTATED_CANARY_BYTES)
            self.assertEqual(live_file.read_bytes(), MUTATED_CANARY_BYTES)

            with self.assertRaises(AssertionError) as ctx:
                assert_closure_bytes(temp_root, frozen_temporary_mapping)

            err_msg = str(ctx.exception)
            self.assertIn("Receipt byte drift detected for", err_msg)
            self.assertIn(str(live_file), err_msg)
            self.assertIn(
                f"expected {len(LITERAL_CANARY_BYTES)} bytes, found {len(MUTATED_CANARY_BYTES)} bytes",
                err_msg,
            )

            self.assertEqual(live_file.read_bytes(), MUTATED_CANARY_BYTES)

            for relpath, expected_bytes in frozen_temporary_mapping.items():
                if relpath != LIVE_CANARY_RELPATH:
                    self.assertEqual((temp_root / relpath).read_bytes(), expected_bytes)

            assert_closure_bytes(repo_root, frozen_canonical_mapping)
            for relpath, expected_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), expected_bytes)

            live_file.write_bytes(frozen_temporary_mapping[LIVE_CANARY_RELPATH])

            assert_closure_bytes(temp_root, frozen_temporary_mapping)
            assert_closure_bytes(repo_root, frozen_canonical_mapping)

            for relpath, expected_bytes in frozen_temporary_mapping.items():
                self.assertEqual((temp_root / relpath).read_bytes(), expected_bytes)

            for relpath, expected_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), expected_bytes)

            temp_dir.cleanup()
            self.assertFalse(temp_root.exists())

            assert_closure_bytes(repo_root, frozen_canonical_mapping)
            for relpath, expected_bytes in frozen_canonical_mapping.items():
                self.assertEqual((repo_root / relpath).read_bytes(), expected_bytes)
        finally:
            if temp_root.exists():
                temp_dir.cleanup()
