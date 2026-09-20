from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.two_site_proposal_runner import (
    check,
    load_task,
    MAX_CONTEXT,
    ProposalError,
    _TASK_BOUNDARY_CODES,
    _TaskBoundaryError,
    unique_json,
)

RUNNER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/two_site_proposal_runner.py"


class TwoSiteProposalTaskPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_scope_env = {
            var: os.environ.pop(var, None)
            for var in (
                "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
                "TWO_SITE_ASSIGNMENT_ID",
                "TWO_SITE_ASSIGNMENT_SHA256",
            )
        }

    def tearDown(self) -> None:
        for var, val in self._saved_scope_env.items():
            if val is not None:
                os.environ[var] = val
            else:
                os.environ.pop(var, None)

    def _create_fixture_project(self, root: Path, task_override: dict | None = None) -> tuple[Path, dict]:
        for var in (
            "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
            "TWO_SITE_ASSIGNMENT_ID",
            "TWO_SITE_ASSIGNMENT_SHA256",
        ):
            os.environ.pop(var, None)
        source_path = root / "example.py"
        source_content = "# Clean example source without secrets\nprint('ready')\n"
        source_path.write_text(source_content, encoding="utf-8")

        task = {
            "task_id": "TASK-PREFLIGHT-001",
            "goal": "Run hermetic preflight validation test",
            "context_files": ["example.py"],
            "allowed_files": ["example.py"],
            "requirements": ["1. Preflight requirement"],
            "test_selectors": ["tests.test_example"],
        }
        if task_override:
            task.update(task_override)

        task_file = root / "task.json"
        task_file.write_text(json.dumps(task), encoding="utf-8")
        return task_file, task

    def test_check_mode_success_and_byte_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve(strict=True)
            task_file, task = self._create_fixture_project(root)

            res = check(root, task_file)
            self.assertEqual(res["state"], "TASK_READY")
            self.assertEqual(res["task_id"], task["task_id"])
            self.assertEqual(res["source_file_count"], 1)

            proc = subprocess.run(
                [sys.executable, str(RUNNER_SCRIPT), "check", "--task", str(task_file), "--root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, msg=f"check CLI failed: {proc.stderr}")
            data = json.loads(proc.stdout)
            self.assertEqual(data["state"], "TASK_READY")
            self.assertEqual(data["task_id"], task["task_id"])
            self.assertEqual(data["source_file_count"], 1)
            self.assertEqual(data["byte_count"], res["byte_count"])
            self.assertNotIn("print('ready')", proc.stdout)

            proposals_dir = root / ".local/two-site-refactor/proposals"
            self.assertFalse(proposals_dir.exists())

    def test_check_hardlink_failure_before_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve(strict=True)
            task_file, _ = self._create_fixture_project(root)

            hardlinked_task = root / "task_hardlink.json"
            os.link(task_file, hardlinked_task)

            with self.assertRaises(ProposalError) as ctx:
                check(root, hardlinked_task)
            self.assertIn("hardlinked", str(ctx.exception))

            proc = subprocess.run(
                [sys.executable, str(RUNNER_SCRIPT), "check", "--task", str(hardlinked_task), "--root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            self.assertEqual(data["state"], "BLOCKED")
            self.assertIn("hardlinked", data["reason"])

            proposals_dir = root / ".local/two-site-refactor/proposals"
            self.assertFalse(proposals_dir.exists())

    def test_check_oversized_task_failure_before_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve(strict=True)
            big_file = root / "big_file.py"
            big_file.write_bytes(b"# padding\n" * ((MAX_CONTEXT // 10) + 10))

            task_file, _ = self._create_fixture_project(root, {
                "context_files": ["big_file.py"],
                "allowed_files": ["big_file.py"],
            })

            with self.assertRaises(ProposalError) as ctx:
                check(root, task_file)
            self.assertIn("budget", str(ctx.exception))

            proc = subprocess.run(
                [sys.executable, str(RUNNER_SCRIPT), "check", "--task", str(task_file), "--root", str(root)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            self.assertEqual(data["state"], "BLOCKED")
            self.assertIn("budget", data["reason"])

            proposals_dir = root / ".local/two-site-refactor/proposals"
            self.assertFalse(proposals_dir.exists())

    def test_check_incompatible_arguments_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve(strict=True)
            task_file, _ = self._create_fixture_project(root)

            incompatible_cases = [
                ["--run-id", "RUN-1234"],
                ["--feedback", str(task_file)],
                ["--proposal", str(task_file)],
                ["--cli", "/usr/bin/echo"],
            ]
            for extra_args in incompatible_cases:
                proc = subprocess.run(
                    [sys.executable, str(RUNNER_SCRIPT), "check", "--task", str(task_file), "--root", str(root), *extra_args],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 1, msg=f"Expected rejection for args: {extra_args}")
                data = json.loads(proc.stdout)
                self.assertEqual(data["state"], "BLOCKED")
                self.assertIn("check mode does not accept", data["reason"])

    def test_worker_verifier_missing_run_id_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve(strict=True)
            task_file, _ = self._create_fixture_project(root)

            for role in ("worker", "verifier"):
                proc = subprocess.run(
                    [sys.executable, str(RUNNER_SCRIPT), role, "--task", str(task_file), "--root", str(root)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 2, msg=f"Expected argparse failure for role {role} missing --run-id")
                self.assertIn("--run-id", proc.stderr)

    def test_finite_boundary_contract_every_reviewed_code(self) -> None:
        self.assertTrue(len(_TASK_BOUNDARY_CODES) >= 8)
        for code in sorted(_TASK_BOUNDARY_CODES):
            err = _TaskBoundaryError(code)
            self.assertIsInstance(err, ProposalError)
            self.assertIsInstance(err, _TaskBoundaryError)
            self.assertEqual(err.code, code)
            self.assertIsNone(err.path)
            self.assertEqual(err.fact, {"code": code, "path": None})
            self.assertEqual(err.fact(), {"code": code, "path": None})
            self.assertEqual(err.to_fact(), {"code": code, "path": None})
            self.assertEqual(set(err.fact.keys()), {"code", "path"})
            self.assertIn(code, str(err))

            err_with_path = _TaskBoundaryError(code, "example.py")
            self.assertIsInstance(err_with_path, ProposalError)
            self.assertEqual(err_with_path.code, code)
            self.assertEqual(err_with_path.path, "example.py")
            self.assertEqual(err_with_path.fact, {"code": code, "path": "example.py"})
            self.assertEqual(err_with_path.fact(), {"code": code, "path": "example.py"})
            self.assertEqual(set(err_with_path.fact.keys()), {"code", "path"})
            self.assertIn(code, str(err_with_path))
            self.assertIn("example.py", str(err_with_path))

    def test_finite_boundary_contract_deterministic_canonical_fact_sanitization(self) -> None:
        err = _TaskBoundaryError("TASK_FILE_NOT_FOUND", "pkg/module.py")
        fact = err.fact
        self.assertEqual(fact, {"code": "TASK_FILE_NOT_FOUND", "path": "pkg/module.py"})
        self.assertEqual(set(fact.keys()), {"code", "path"})
        for forbidden in ("source", "prompt", "command", "traceback", "/"):
            self.assertNotIn(forbidden, fact)
        self.assertFalse(str(err).startswith("/"))

    def test_finite_boundary_contract_invalid_code_and_metadata_rejection(self) -> None:
        valid_code = next(iter(_TASK_BOUNDARY_CODES))

        with self.assertRaises(ProposalError):
            _TaskBoundaryError(None)  # type: ignore[arg-type]
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(12345)  # type: ignore[arg-type]
        with self.assertRaises(ProposalError):
            _TaskBoundaryError("NOT_A_REVIEWED_CODE")
        with self.assertRaises(ProposalError):
            _TaskBoundaryError("")

        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, {"metadata": "dict"})  # type: ignore[arg-type]
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "example.py", "extra_arg")
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "example.py", extra="metadata")
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, metadata={"extra": "field"})

    def test_finite_boundary_contract_invalid_path_rejection(self) -> None:
        valid_code = next(iter(_TASK_BOUNDARY_CODES))

        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "/root/example.py")
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "/etc/passwd")

        for bad_path in (
            "../escape.py",
            "dir/../escape.py",
            "./example.py",
            "dir/./example.py",
            ".",
            "..",
            "",
            "dir//file.py",
            "dir/",
        ):
            with self.assertRaises(ProposalError, msg=f"Should reject bad path: {bad_path!r}"):
                _TaskBoundaryError(valid_code, bad_path)

        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "dir\\file.py")
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "dir/file\0.py")

        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "sk-" + "A" * 30)
        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "ghp_" + "B" * 30)

        with self.assertRaises(ProposalError):
            _TaskBoundaryError(valid_code, "a" * 257)

    def test_load_task_invalid_json_boundary_errors_and_unique_json_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir).resolve(strict=True)
            for var in (
                "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
                "TWO_SITE_ASSIGNMENT_ID",
                "TWO_SITE_ASSIGNMENT_SHA256",
            ):
                os.environ.pop(var, None)

            task_file, task = self._create_fixture_project(root)

            task_file.write_bytes(b"\x80\x81\xff\xfe")
            with self.assertRaises(_TaskBoundaryError) as ctx_utf8:
                load_task(root, task_file)
            self.assertEqual(ctx_utf8.exception.code, "TASK_SCHEMA_INVALID")
            self.assertIsNone(ctx_utf8.exception.path)
            self.assertIsInstance(ctx_utf8.exception, ProposalError)

            with self.assertRaises(_TaskBoundaryError) as ctx_check_utf8:
                check(root, task_file)
            self.assertEqual(ctx_check_utf8.exception.code, "TASK_SCHEMA_INVALID")

            task_file.write_text('{"task_id": "TASK-001", "goal": ', encoding="utf-8")
            with self.assertRaises(_TaskBoundaryError) as ctx_malformed:
                load_task(root, task_file)
            self.assertEqual(ctx_malformed.exception.code, "TASK_SCHEMA_INVALID")
            self.assertIsNone(ctx_malformed.exception.path)
            self.assertIsInstance(ctx_malformed.exception, ProposalError)

            with self.assertRaises(_TaskBoundaryError) as ctx_check_malformed:
                check(root, task_file)
            self.assertEqual(ctx_check_malformed.exception.code, "TASK_SCHEMA_INVALID")

            valid_task_copy = dict(task)
            dup_key_json = json.dumps(valid_task_copy)[:-1] + ', "task_id": "DUPLICATE-ID"}'
            task_file.write_text(dup_key_json, encoding="utf-8")
            with self.assertRaises(_TaskBoundaryError) as ctx_dup:
                load_task(root, task_file)
            self.assertEqual(ctx_dup.exception.code, "TASK_SCHEMA_INVALID")
            self.assertIsNone(ctx_dup.exception.path)
            self.assertIsInstance(ctx_dup.exception, ProposalError)

            with self.assertRaises(_TaskBoundaryError) as ctx_check_dup:
                check(root, task_file)
            self.assertEqual(ctx_check_dup.exception.code, "TASK_SCHEMA_INVALID")

            with self.assertRaises(ProposalError) as ctx_generic:
                unique_json('{"dup": 1, "dup": 2}')
            self.assertEqual(type(ctx_generic.exception), ProposalError)
            self.assertNotIsInstance(ctx_generic.exception, _TaskBoundaryError)
            self.assertEqual(str(ctx_generic.exception), "duplicate JSON key")

            task_file.write_text(json.dumps(task), encoding="utf-8")
            res = check(root, task_file)
            self.assertEqual(res["state"], "TASK_READY")
            self.assertEqual(res["task_id"], task["task_id"])


if __name__ == "__main__":
    unittest.main()
