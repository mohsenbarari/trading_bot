import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_staging_offer_overtime_acceptance as runner


class StagingOfferOvertimeAcceptanceTests(unittest.TestCase):
    def test_default_branch_is_offer_overtime_candidate(self):
        args = runner.parse_args([])
        self.assertEqual(args.expected_branch, "candidate/offer-overtime")

    def test_catalog_covers_stage16_required_axes(self):
        ids = {item["id"] for item in runner.SCENARIOS}
        for required in (
            "OT-PREF-WEBAPP-SAVE",
            "OT-PREF-BOT-SAVE",
            "OT-PREF-DISABLED-REGRESSION",
            "OT-REQ-CROSS-FORWARD",
            "OT-QUEUE-ORDER",
            "OT-FINAL-TAIL",
            "OT-CHANNEL-MARKER",
            "OT-SYNC-RECOVERY",
            "OT-UI-RECONNECT",
        ):
            self.assertIn(required, ids)

    def test_plan_writes_evidence_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "plan-run"
            args = runner.parse_args(
                [
                    "--mode",
                    "plan",
                    "--artifact-dir",
                    str(artifact_dir),
                    "--expected-branch",
                    "candidate/offer-overtime",
                ]
            )
            summary = runner.run_plan(args)
            self.assertEqual(summary["status"], "plan_ready")
            self.assertTrue((artifact_dir / "manifest.json").is_file())
            self.assertTrue(artifact_dir.with_suffix(".zip").is_file())

    def test_execute_is_fail_closed_without_confirm_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "execute-run"
            args = runner.parse_args(
                [
                    "--mode",
                    "execute",
                    "--artifact-dir",
                    str(artifact_dir),
                ]
            )
            env = {
                key: value
                for key, value in os.environ.items()
                if key != runner.EXECUTION_CONFIRM_ENV
            }
            with patch.dict(os.environ, env, clear=True):
                summary, code = runner.run_execute(args)
            self.assertEqual(code, 2)
            self.assertEqual(summary["status"], "execute_blocked")

    def test_preflight_fails_on_wrong_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "preflight-run"
            args = runner.parse_args(
                [
                    "--mode",
                    "preflight",
                    "--artifact-dir",
                    str(artifact_dir),
                    "--expected-branch",
                    "candidate/does-not-exist",
                    "--expected-release-sha",
                    "deadbeef",
                ]
            )
            with patch.object(runner, "check_tls") as tls, patch.object(
                runner, "check_http_json"
            ) as http, patch.object(
                runner, "check_foreign_public_surface_guard"
            ) as guard, patch.object(
                runner, "check_internal_ingress_without_basic_auth"
            ) as ingress:
                tls.return_value = runner.CheckResult("tls", "passed", "ok")
                http.return_value = runner.CheckResult("http", "passed", "ok")
                guard.return_value = runner.CheckResult("guard", "passed", "ok")
                ingress.return_value = runner.CheckResult("ingress", "passed", "ok")
                summary, code = runner.run_preflight(args)
            self.assertEqual(code, 1)
            self.assertEqual(summary["status"], "preflight_failed")
            self.assertIn("git_branch", summary["failed_checks"])


if __name__ == "__main__":
    unittest.main()
