import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_SCOPE_VARS = (
    "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
    "TWO_SITE_ASSIGNMENT_ID",
    "TWO_SITE_ASSIGNMENT_SHA256",
)

for _var in _SCOPE_VARS:
    os.environ.pop(_var, None)

from scripts.two_site_proposal_runner import (
    MAX_CONTEXT,
    ProposalError,
    _TaskBoundaryError,
    bounded_file_bytes,
    check,
    load_task,
)


def _make_task(
    context_files: list[str] | None = None,
    allowed_files: list[str] | None = None,
) -> dict:
    ctx = context_files or ["ctx.py"]
    return {
        "task_id": "test-task",
        "goal": "Hermetic test goal",
        "context_files": ctx,
        "allowed_files": allowed_files or ctx,
        "requirements": ["R1"],
        "test_selectors": ["tests/test_two_site_safe_reader_mapping.py"],
    }


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

    def test_task_file_facts(self) -> None:
        missing = self.root / "missing.json"
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, missing)
        self.assertEqual(ctx.exception.code, "TASK_FILE_NOT_FOUND")
        self.assertEqual(ctx.exception.path, "missing.json")

        real = self.root / "real.json"
        real.write_text(json.dumps(_make_task()))
        sym = self.root / "sym.json"
        sym.symlink_to(real)
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, sym)
        self.assertEqual(ctx.exception.code, "TASK_FILE_SYMLINK")
        self.assertEqual(ctx.exception.path, "sym.json")

        d = self.root / "d.json"
        d.mkdir()
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, d)
        self.assertEqual(ctx.exception.code, "TASK_FILE_NOT_REGULAR")
        self.assertEqual(ctx.exception.path, "d.json")

        hl = self.root / "hl.json"
        os.link(real, hl)
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, hl)
        self.assertEqual(ctx.exception.code, "TASK_FILE_HARDLINKED")
        self.assertEqual(ctx.exception.path, "hl.json")

        over = self.root / "over.json"
        over.write_bytes(b" " * (MAX_CONTEXT + 1))
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, over)
        self.assertEqual(ctx.exception.code, "TASK_BUDGET_EXCEEDED")
        self.assertEqual(ctx.exception.path, "over.json")

    def test_context_file_facts(self) -> None:
        def _task(name: str) -> Path:
            p = self.root / f"task_{name}.json"
            p.write_text(json.dumps(_make_task([name])))
            return p

        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, _task("missing_ctx.py"))
        self.assertEqual(ctx.exception.code, "TASK_FILE_NOT_FOUND")
        self.assertEqual(ctx.exception.path, "missing_ctx.py")

        real = self.root / "real_ctx.py"
        real.write_text("# ctx\n")
        sym = self.root / "sym_ctx.py"
        sym.symlink_to(real)
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, _task("sym_ctx.py"))
        self.assertEqual(ctx.exception.code, "TASK_FILE_SYMLINK")
        self.assertEqual(ctx.exception.path, "sym_ctx.py")

        d = self.root / "dir_ctx.py"
        d.mkdir()
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, _task("dir_ctx.py"))
        self.assertEqual(ctx.exception.code, "TASK_FILE_NOT_REGULAR")
        self.assertEqual(ctx.exception.path, "dir_ctx.py")

        hl = self.root / "hl_ctx.py"
        os.link(real, hl)
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, _task("hl_ctx.py"))
        self.assertEqual(ctx.exception.code, "TASK_FILE_HARDLINKED")
        self.assertEqual(ctx.exception.path, "hl_ctx.py")

        over = self.root / "over_ctx.py"
        over.write_bytes(b"#" * (MAX_CONTEXT + 1))
        with self.assertRaises(_TaskBoundaryError) as ctx:
            load_task(self.root, _task("over_ctx.py"))
        self.assertEqual(ctx.exception.code, "TASK_BUDGET_EXCEEDED")
        self.assertEqual(ctx.exception.path, "over_ctx.py")

    def test_fatal_first_context_prevents_later_read(self) -> None:
        orig = self.root / "first_orig.py"
        orig.write_text("# first\n")
        hl = self.root / "first_hl.py"
        os.link(orig, hl)
        (self.root / "second.py").write_text("# second\n")

        task_path = self.root / "task_multi.json"
        task_path.write_text(json.dumps(_make_task(["first_hl.py", "second.py"])))

        labels: list[str] = []
        orig_reader = bounded_file_bytes

        def spy(root, path, limit, label, failure_callback=None):
            labels.append(label)
            return orig_reader(root, path, limit, label, failure_callback=failure_callback)

        with patch("scripts.two_site_proposal_runner.bounded_file_bytes", side_effect=spy):
            with self.assertRaises(_TaskBoundaryError) as ctx:
                load_task(self.root, task_path)
            self.assertEqual(ctx.exception.code, "TASK_FILE_HARDLINKED")
            self.assertEqual(ctx.exception.path, "first_hl.py")

        self.assertIn("context file first_hl.py", labels)
        self.assertNotIn("context file second.py", labels)

    def test_allowed_only_missing_and_check_task_ready(self) -> None:
        (self.root / "ctx.py").write_text("# ctx\n")
        task_data = _make_task(["ctx.py"], ["ctx.py", "allowed_missing.py"])
        task_path = self.root / "task.json"
        task_path.write_text(json.dumps(task_data))

        task, sources = load_task(self.root, task_path)
        self.assertIn("allowed_missing.py", sources)
        self.assertIsNone(sources["allowed_missing.py"]["sha256"])
        self.assertIsNone(sources["allowed_missing.py"]["content"])
        self.assertIsNotNone(sources["ctx.py"]["sha256"])
        self.assertIsNotNone(sources["ctx.py"]["content"])

        readiness = check(self.root, task_path)
        self.assertEqual(readiness["state"], "TASK_READY")
        self.assertEqual(readiness["task_id"], task_data["task_id"])


if __name__ == "__main__":
    unittest.main()
