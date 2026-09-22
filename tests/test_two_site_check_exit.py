import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from scripts.two_site_proposal_runner import ROOT, main


class TwoSiteCheckExitTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(os.environ)
        for var in (
            "TWO_SITE_ASSIGNMENT_SCOPE_REQUIRED",
            "TWO_SITE_ASSIGNMENT_ID",
            "TWO_SITE_ASSIGNMENT_SHA256",
            "TWO_SITE_AUTOPILOT_RUN_ID",
        ):
            self.env.pop(var, None)
        self.env["APP_ENV_FILE"] = "config/unit-test.env.example"

    def _run_cli(self, task_file: Path, root: Path) -> subprocess.CompletedProcess:
        cmd = [
            sys.executable,
            str(ROOT / "scripts/two_site_proposal_runner.py"),
            "check",
            "--task",
            str(task_file),
            "--root",
            str(root),
        ]
        return subprocess.run(cmd, capture_output=True, text=True, env=self.env)

    def test_check_subprocess_valid_and_fatal_scenarios(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            td = Path(tmpdir)
            ctx = td / "dummy.py"
            ctx.write_text("x = 1\n", encoding="utf-8")
            proposals = td / ".local/two-site-refactor/proposals"

            valid_task = td / "valid_task.json"
            valid_task.write_text(
                json.dumps({
                    "task_id": "T1",
                    "goal": "goal",
                    "context_files": ["dummy.py"],
                    "allowed_files": ["dummy.py"],
                    "requirements": ["r1"],
                    "test_selectors": ["tests/test_x.py"],
                }),
                encoding="utf-8",
            )
            res = self._run_cli(valid_task, td)
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(data.get("state"), "TASK_READY")
            self.assertFalse(proposals.exists())

            malformed = td / "bad.json"
            malformed.write_text("{not valid json", encoding="utf-8")
            res_m = self._run_cli(malformed, td)
            self.assertEqual(res_m.returncode, 1)
            data_m = json.loads(res_m.stdout)
            self.assertEqual(data_m.get("state"), "TASK_BLOCKED")
            self.assertEqual(data_m["issues"][0]["code"], "TASK_SCHEMA_INVALID")
            self.assertNotIn("dummy.py", res_m.stdout)
            self.assertNotIn("dummy.py", res_m.stderr)
            self.assertFalse(proposals.exists())

            hardlink = td / "hl_task.json"
            os.link(valid_task, hardlink)
            res_h = self._run_cli(hardlink, td)
            self.assertEqual(res_h.returncode, 1)
            data_h = json.loads(res_h.stdout)
            self.assertEqual(data_h.get("state"), "TASK_BLOCKED")
            self.assertEqual(data_h["issues"][0]["code"], "TASK_FILE_HARDLINKED")
            self.assertNotIn("x = 1", res_h.stdout)
            self.assertNotIn("x = 1", res_h.stderr)
            self.assertFalse(proposals.exists())

    def test_mocked_main_exits_one_for_nonready_payloads(self):
        for payload in (
            {"state": "TASK_BLOCKED", "issues": []},
            {"state": "UNKNOWN"},
            {"state": "PROPOSAL_READY"},
        ):
            with patch("sys.argv", ["runner", "check", "--task", "t.json"]), \
                 patch("scripts.two_site_proposal_runner.check", return_value=payload):
                self.assertEqual(main(), 1)
        with patch("sys.argv", ["runner", "check", "--task", "t.json"]), \
             patch("scripts.two_site_proposal_runner.check", return_value={"state": "TASK_READY"}):
            self.assertEqual(main(), 0)
