"""Shared physical prefix-reader helper and witnesses for the P1-00b stage ledger."""
from __future__ import annotations

import hashlib
import pathlib
import stat
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
LEDGER_REL_PATH = "docs/refactor/two-server/section-1/00-stage-ledger.md"
LEDGER_PATH = ROOT / LEDGER_REL_PATH
HISTORICAL_PREFIX_BYTES = 109329
HISTORICAL_PREFIX_SHA256 = (
    "a25deecb4880ed4c7ceb905ae5db5048c8d3fc7b58129bc6cfdcf4d31d2c05c2"
)
APPENDED_SUFFIX = (
    b"\n## Additive Ledger Suffix Witness\n"
    b"- status: trailing-bytes-allowed-and-unhashed\n"
)


def read_ledger_prefix(
    path: pathlib.Path, prefix_bytes: int, prefix_sha256: str
) -> bytes:
    if not isinstance(path, pathlib.Path):
        raise AssertionError(f"Expected pathlib.Path, got {type(path)!r}")
    if (
        not isinstance(prefix_bytes, int)
        or isinstance(prefix_bytes, bool)
        or prefix_bytes <= 0
    ):
        raise AssertionError(f"Invalid prefix_bytes: {prefix_bytes!r}")
    if not isinstance(prefix_sha256, str) or not prefix_sha256:
        raise AssertionError("Invalid prefix_sha256")
    if path.is_symlink():
        raise AssertionError(f"Symlink ledger path rejected: {path}")
    try:
        info = path.stat()
    except OSError as exc:
        raise AssertionError(f"Cannot stat ledger path: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise AssertionError(f"Non-regular ledger path rejected: {path}")
    if info.st_size < prefix_bytes:
        raise AssertionError(
            f"Ledger size {info.st_size} shorter than required prefix {prefix_bytes}"
        )
    with path.open("rb") as handle:
        prefix = handle.read(prefix_bytes)
    if len(prefix) != prefix_bytes:
        raise AssertionError(
            f"Actual read {len(prefix)} shorter than required prefix {prefix_bytes}"
        )
    actual_sha256 = hashlib.sha256(prefix).hexdigest()
    if actual_sha256 != prefix_sha256:
        raise AssertionError(
            f"Ledger prefix SHA256 mismatch: {actual_sha256} != {prefix_sha256}"
        )
    return prefix


class LedgerPrefixReaderTests(unittest.TestCase):
    def _load_canonical_historical_prefix_independently(self) -> tuple[bytes, bytes]:
        self.assertFalse(LEDGER_PATH.is_symlink())
        info = LEDGER_PATH.stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertGreaterEqual(info.st_size, HISTORICAL_PREFIX_BYTES)
        full_repo_bytes = LEDGER_PATH.read_bytes()
        self.assertEqual(len(full_repo_bytes), info.st_size)
        historical_prefix = full_repo_bytes[:HISTORICAL_PREFIX_BYTES]
        self.assertEqual(len(historical_prefix), HISTORICAL_PREFIX_BYTES)
        self.assertEqual(
            hashlib.sha256(historical_prefix).hexdigest(),
            HISTORICAL_PREFIX_SHA256,
        )
        return historical_prefix, full_repo_bytes

    def test_append_preserves_exact_historical_prefix(self) -> None:
        expected_prefix, repo_before = (
            self._load_canonical_historical_prefix_independently()
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            exact_file = tmp_root / "00-stage-ledger-exact.md"
            appended_file = tmp_root / "00-stage-ledger-appended.md"

            exact_file.write_bytes(expected_prefix)
            appended_file.write_bytes(expected_prefix + APPENDED_SUFFIX)

            self.assertEqual(exact_file.stat().st_size, HISTORICAL_PREFIX_BYTES)
            self.assertEqual(
                appended_file.stat().st_size,
                HISTORICAL_PREFIX_BYTES + len(APPENDED_SUFFIX),
            )
            physical_appended = appended_file.read_bytes()
            self.assertEqual(physical_appended[:HISTORICAL_PREFIX_BYTES], expected_prefix)
            self.assertEqual(physical_appended[HISTORICAL_PREFIX_BYTES:], APPENDED_SUFFIX)

            exact_result = read_ledger_prefix(
                exact_file, HISTORICAL_PREFIX_BYTES, HISTORICAL_PREFIX_SHA256
            )
            appended_result = read_ledger_prefix(
                appended_file, HISTORICAL_PREFIX_BYTES, HISTORICAL_PREFIX_SHA256
            )

            self.assertEqual(exact_result, expected_prefix)
            self.assertEqual(appended_result, expected_prefix)
            self.assertEqual(appended_result, exact_result)
            self.assertEqual(len(appended_result), HISTORICAL_PREFIX_BYTES)
            self.assertEqual(
                hashlib.sha256(appended_result).hexdigest(),
                HISTORICAL_PREFIX_SHA256,
            )

        self.assertEqual(LEDGER_PATH.read_bytes(), repo_before)

    def test_changed_prefix_is_rejected_with_append(self) -> None:
        expected_prefix, repo_before = (
            self._load_canonical_historical_prefix_independently()
        )
        mutated = bytearray(expected_prefix)
        mutated[HISTORICAL_PREFIX_BYTES // 2] ^= 0x01
        mutated_prefix = bytes(mutated)
        self.assertEqual(len(mutated_prefix), HISTORICAL_PREFIX_BYTES)
        self.assertNotEqual(mutated_prefix, expected_prefix)
        self.assertNotEqual(
            hashlib.sha256(mutated_prefix).hexdigest(),
            HISTORICAL_PREFIX_SHA256,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            mutated_appended_file = tmp_root / "00-stage-ledger-mutated-appended.md"
            mutated_appended_file.write_bytes(mutated_prefix + APPENDED_SUFFIX)

            self.assertEqual(
                mutated_appended_file.stat().st_size,
                HISTORICAL_PREFIX_BYTES + len(APPENDED_SUFFIX),
            )
            self.assertEqual(
                mutated_appended_file.read_bytes()[HISTORICAL_PREFIX_BYTES:],
                APPENDED_SUFFIX,
            )
            with self.assertRaises(AssertionError):
                read_ledger_prefix(
                    mutated_appended_file,
                    HISTORICAL_PREFIX_BYTES,
                    HISTORICAL_PREFIX_SHA256,
                )

        self.assertEqual(LEDGER_PATH.read_bytes(), repo_before)

    def test_truncated_prefix_is_rejected(self) -> None:
        expected_prefix, repo_before = (
            self._load_canonical_historical_prefix_independently()
        )
        truncated_prefix = expected_prefix[:-1]
        self.assertEqual(len(truncated_prefix), HISTORICAL_PREFIX_BYTES - 1)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_root = pathlib.Path(tmp_dir)
            truncated_file = tmp_root / "00-stage-ledger-truncated.md"
            symlink_file = tmp_root / "00-stage-ledger-symlink.md"
            valid_file = tmp_root / "00-stage-ledger-valid.md"

            truncated_file.write_bytes(truncated_prefix)
            self.assertEqual(truncated_file.stat().st_size, HISTORICAL_PREFIX_BYTES - 1)
            with self.assertRaises(AssertionError):
                read_ledger_prefix(
                    truncated_file,
                    HISTORICAL_PREFIX_BYTES,
                    HISTORICAL_PREFIX_SHA256,
                )

            valid_file.write_bytes(expected_prefix)
            symlink_file.symlink_to(valid_file)
            with self.assertRaises(AssertionError):
                read_ledger_prefix(
                    symlink_file,
                    HISTORICAL_PREFIX_BYTES,
                    HISTORICAL_PREFIX_SHA256,
                )
            with self.assertRaises(AssertionError):
                read_ledger_prefix(
                    tmp_root,
                    HISTORICAL_PREFIX_BYTES,
                    HISTORICAL_PREFIX_SHA256,
                )

        self.assertEqual(LEDGER_PATH.read_bytes(), repo_before)


if __name__ == "__main__":
    unittest.main()
