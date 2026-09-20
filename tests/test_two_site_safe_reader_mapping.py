import os
from pathlib import Path
import tempfile
import unittest

_SCOPE_VARS = (
    "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
    "TWO_SITE_ASSIGNMENT_ID",
    "TWO_SITE_ASSIGNMENT_SHA256",
)

for _var in _SCOPE_VARS:
    os.environ.pop(_var, None)

from scripts.two_site_proposal_runner import (
    ProposalError,
    _TaskBoundaryError,
    bounded_file_bytes,
)


class TestTwoSiteSafeReaderMapping(unittest.TestCase):
    def setUp(self) -> None:
        for var in _SCOPE_VARS:
            os.environ.pop(var, None)
        self.temp_dir = tempfile.TemporaryDirectory(prefix="safe-reader-test-")
        self.root = Path(self.temp_dir.name).resolve(strict=True)

    def tearDown(self) -> None:
        for var in _SCOPE_VARS:
            os.environ.pop(var, None)
        self.temp_dir.cleanup()

    @staticmethod
    def _callback(code: str, path: str | None) -> _TaskBoundaryError:
        return _TaskBoundaryError(code, path)

    def test_opt_in_outside_root(self) -> None:
        outside_path = self.root.parent / "outside.txt"
        with self.assertRaises(_TaskBoundaryError) as ctx:
            bounded_file_bytes(
                self.root,
                outside_path,
                1024,
                "test_outside",
                failure_callback=self._callback,
            )
        self.assertEqual(ctx.exception.code, "TASK_FILE_OUTSIDE_ROOT")
        self.assertIsNone(ctx.exception.path)

    def test_opt_in_missing(self) -> None:
        missing_path = Path("missing.txt")
        with self.assertRaises(_TaskBoundaryError) as ctx:
            bounded_file_bytes(
                self.root,
                missing_path,
                1024,
                "test_missing",
                failure_callback=self._callback,
            )
        self.assertEqual(ctx.exception.code, "TASK_FILE_NOT_FOUND")
        self.assertEqual(ctx.exception.path, "missing.txt")

    def test_opt_in_symlink(self) -> None:
        target = self.root / "target.txt"
        target.write_bytes(b"target contents")
        link = self.root / "link.txt"
        link.symlink_to(target)
        with self.assertRaises(_TaskBoundaryError) as ctx:
            bounded_file_bytes(
                self.root,
                Path("link.txt"),
                1024,
                "test_symlink",
                failure_callback=self._callback,
            )
        self.assertEqual(ctx.exception.code, "TASK_FILE_SYMLINK")
        self.assertEqual(ctx.exception.path, "link.txt")

    def test_opt_in_directory(self) -> None:
        sub_dir = self.root / "sub_dir"
        sub_dir.mkdir()
        with self.assertRaises(_TaskBoundaryError) as ctx:
            bounded_file_bytes(
                self.root,
                Path("sub_dir"),
                1024,
                "test_directory",
                failure_callback=self._callback,
            )
        self.assertEqual(ctx.exception.code, "TASK_FILE_NOT_REGULAR")
        self.assertEqual(ctx.exception.path, "sub_dir")

    def test_opt_in_hardlink(self) -> None:
        original = self.root / "original.txt"
        original.write_bytes(b"original contents")
        hardlink = self.root / "hardlink.txt"
        os.link(original, hardlink)
        with self.assertRaises(_TaskBoundaryError) as ctx:
            bounded_file_bytes(
                self.root,
                Path("hardlink.txt"),
                1024,
                "test_hardlink",
                failure_callback=self._callback,
            )
        self.assertEqual(ctx.exception.code, "TASK_FILE_HARDLINKED")
        self.assertEqual(ctx.exception.path, "hardlink.txt")

    def test_opt_in_budget_exceeded(self) -> None:
        large = self.root / "large.txt"
        large.write_bytes(b"0123456789ABCDEF")
        with self.assertRaises(_TaskBoundaryError) as ctx:
            bounded_file_bytes(
                self.root,
                Path("large.txt"),
                5,
                "test_budget",
                failure_callback=self._callback,
            )
        self.assertEqual(ctx.exception.code, "TASK_BUDGET_EXCEEDED")
        self.assertEqual(ctx.exception.path, "large.txt")

    def test_default_untyped_preservation(self) -> None:
        original = self.root / "orig.txt"
        original.write_bytes(b"some content")
        hardlink = self.root / "hl.txt"
        os.link(original, hardlink)

        with self.assertRaises(ProposalError) as ctx_hl:
            bounded_file_bytes(self.root, Path("hl.txt"), 1024, "target_hl")
        self.assertNotIsInstance(ctx_hl.exception, _TaskBoundaryError)
        self.assertEqual(type(ctx_hl.exception), ProposalError)
        self.assertEqual(str(ctx_hl.exception), "target_hl is hardlinked and cannot be trusted")

        link = self.root / "sym.txt"
        link.symlink_to(original)
        with self.assertRaises(ProposalError) as ctx_sym:
            bounded_file_bytes(self.root, Path("sym.txt"), 1024, "target_sym")
        self.assertNotIsInstance(ctx_sym.exception, _TaskBoundaryError)
        self.assertEqual(type(ctx_sym.exception), ProposalError)
        self.assertEqual(str(ctx_sym.exception), "target_sym is a symlink or path escape")

        large = self.root / "over.txt"
        large.write_bytes(b"content over limit")
        with self.assertRaises(ProposalError) as ctx_budget:
            bounded_file_bytes(self.root, Path("over.txt"), 4, "target_budget")
        self.assertNotIsInstance(ctx_budget.exception, _TaskBoundaryError)
        self.assertEqual(type(ctx_budget.exception), ProposalError)
        self.assertEqual(str(ctx_budget.exception), "target_budget exceeds the safe context budget")


if __name__ == "__main__":
    unittest.main()
