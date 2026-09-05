from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.two_site_proposal_runner import check, MAX_CONTEXT, ProposalError

RUNNER_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/two_site_proposal_runner.py"


class TwoSiteProposalTaskPreflightTests(unittest.TestCase):
    def _create_fixture_project(self, root: Path, task_override: dict | None = None) -> tuple[Path, dict]:
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


if __name__ == "__main__":
    unittest.main()
