import json
from pathlib import Path
import tempfile
import unittest

CANONICAL_C15_RECEIPT_RELPATH = Path(
    "docs/refactor/two-server/section-1/inventory/recent-characterization-reconciliation-c15-from-c17.json"
)


def assert_receipt_bytes_unchanged(receipt_path: Path, expected_bytes: bytes) -> None:
    actual_bytes = Path(receipt_path).read_bytes()
    if actual_bytes != expected_bytes:
        raise AssertionError(
            f"Receipt byte drift detected for {receipt_path}: "
            f"expected {len(expected_bytes)} bytes, found {len(actual_bytes)} bytes"
        )


class ReceiptBytePreservationTests(unittest.TestCase):
    def test_unchanged_receipt_bytes_pass(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        canonical_path = repo_root / CANONICAL_C15_RECEIPT_RELPATH
        self.assertTrue(canonical_path.is_file(), f"Canonical receipt not found: {canonical_path}")
        frozen_bytes = canonical_path.read_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            copy_path = Path(temp_dir) / canonical_path.name
            copy_path.write_bytes(frozen_bytes)

            assert_receipt_bytes_unchanged(copy_path, frozen_bytes)

            self.assertEqual(copy_path.read_bytes(), frozen_bytes)
            self.assertEqual(canonical_path.read_bytes(), frozen_bytes)
            self.assertEqual(copy_path.read_bytes(), canonical_path.read_bytes())

    def test_same_json_whitespace_drift_rejected(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        canonical_path = repo_root / CANONICAL_C15_RECEIPT_RELPATH
        self.assertTrue(canonical_path.is_file(), f"Canonical receipt not found: {canonical_path}")
        frozen_bytes = canonical_path.read_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            copy_path = Path(temp_dir) / canonical_path.name
            copy_path.write_bytes(frozen_bytes)

            assert_receipt_bytes_unchanged(copy_path, frozen_bytes)
            self.assertEqual(copy_path.read_bytes(), frozen_bytes)

            drifted_bytes = frozen_bytes + b"\n"
            copy_path.write_bytes(drifted_bytes)

            self.assertNotEqual(copy_path.read_bytes(), frozen_bytes)
            original_doc = json.loads(frozen_bytes.decode("utf-8"))
            drifted_doc = json.loads(copy_path.read_bytes().decode("utf-8"))
            self.assertEqual(drifted_doc, original_doc)

            with self.assertRaises(AssertionError):
                assert_receipt_bytes_unchanged(copy_path, frozen_bytes)

            copy_path.write_bytes(frozen_bytes)
            self.assertEqual(copy_path.read_bytes(), frozen_bytes)

            assert_receipt_bytes_unchanged(copy_path, frozen_bytes)

            self.assertEqual(canonical_path.read_bytes(), frozen_bytes)

        self.assertEqual(canonical_path.read_bytes(), frozen_bytes)


if __name__ == "__main__":
    unittest.main()
