import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_sync_parity_stage8_staging_rollout.py"

spec = importlib.util.spec_from_file_location("run_sync_parity_stage8_staging_rollout", MODULE_PATH)
stage8 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = stage8
spec.loader.exec_module(stage8)


def fake_git_value(args):
    if args == ["branch", "--show-current"]:
        return stage8.EXPECTED_BRANCH
    if args == ["rev-parse", "HEAD"]:
        return "abc123"
    return None


class SyncParityStage8StagingRolloutTests(unittest.TestCase):
    def build_args(self, artifact_dir: Path, *extra: str):
        return stage8.parse_args(
            [
                "--prefix",
                "P8_STAGE_UNIT_",
                "--artifact-dir",
                str(artifact_dir),
                *extra,
            ]
        )

    def test_default_plan_is_artifact_only_and_marks_execution_hard_disabled(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            plan = stage8.build_plan(self.build_args(Path(tmp_dir)))

        self.assertEqual(plan["schema_version"], "sync_parity_stage8_staging_rollout_v1")
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["branch_gate"]["passed"])
        self.assertFalse(plan["execute_requested"])
        self.assertFalse(plan["execution_contract"]["production_deploy_allowed"])
        self.assertTrue(plan["execution_contract"]["legacy_two_server_staging_execution_hard_disabled"])
        self.assertEqual(plan["execution_plan"]["status"], "hard_disabled")
        self.assertEqual(
            plan["coverage_contract"]["market_surface_pairs"],
            [
                "webapp_offer__webapp_request",
                "webapp_offer__telegram_request",
                "telegram_offer__webapp_request",
                "telegram_offer__telegram_request",
            ],
        )

        preflight_commands = plan["preflight"]["commands"]
        self.assertTrue(preflight_commands)
        self.assertTrue(all(not command["mutates_staging"] for command in preflight_commands))
        command_names = {command["name"] for command in preflight_commands}
        self.assertIn("local_sync_guarantee_matrix", command_names)
        self.assertIn("local_out_of_order_and_watermark_guards", command_names)
        self.assertIn("staging_parity_snapshot_quick", command_names)
        self.assertIn("staging_parity_snapshot_deep", command_names)
        self.assertIn("candidate_full_matrix_dry_run", command_names)
        self.assertIn("targeted_join_matrix_dry_run", command_names)
        self.assertIn("sync_repair_drift_dry_run", command_names)
        self.assertIn("staging_cleanup_dry_run_for_prefix", command_names)

        execution_commands = plan["execution_plan"]["commands"]
        self.assertTrue(any(command["mutates_staging"] for command in execution_commands))
        self.assertIn(
            "staging_candidate_full_matrix_no_pressure",
            {command["name"] for command in execution_commands},
        )

    def test_repair_fixture_contains_business_drift_for_dry_run_plan(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path, peer_path = stage8.build_repair_fixture_snapshots(Path(tmp_dir))
            local_snapshot = json.loads(local_path.read_text(encoding="utf-8"))
            peer_snapshot = json.loads(peer_path.read_text(encoding="utf-8"))

        self.assertIn("offers", local_snapshot["tables"])
        report = stage8.build_table_parity_snapshot
        self.assertTrue(callable(report))

        from core.sync_parity import compare_parity_snapshots

        comparison = compare_parity_snapshots(local_snapshot, peer_snapshot)
        self.assertEqual(comparison["status"], "business_drift")
        self.assertEqual(comparison["severity_counts"]["business_drift"], 1)

    def test_execute_mode_stays_retired_even_with_the_legacy_confirmation_value(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            args = self.build_args(Path(tmp_dir), "--mode", "execute")
            plan = stage8.build_plan(args)

        with patch.dict(
            os.environ,
            {stage8.EXECUTION_CONFIRM_ENV: stage8.EXECUTION_CONFIRM_VALUE},
            clear=True,
        ):
            executed, exit_code = stage8.execute_plan(plan, include_mutating=True)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_legacy_two_server_stage8_runtime_retired")
        self.assertEqual(executed["execution_plan"]["status"], "hard_disabled")
        self.assertEqual(executed["preflight"]["status"], "hard_disabled")
        self.assertEqual(executed["post_execution_checks"]["status"], "hard_disabled")

    def test_preflight_mode_stays_retired_without_calling_the_command_helper(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            args = self.build_args(Path(tmp_dir), "--mode", "preflight")
            plan = stage8.build_plan(args)

        with patch.object(stage8, "run_command") as run_command:
            executed, exit_code = stage8.execute_plan(plan, include_mutating=False)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_legacy_two_server_stage8_runtime_retired")
        self.assertFalse(run_command.called)

    def test_direct_command_helper_cannot_be_reenabled_by_importers(self):
        command = {
            "name": "unsafe-direct-import",
            "args": ["unsafe-command"],
            "timeout_seconds": 1,
        }
        with patch.object(stage8.subprocess, "run") as run:
            with self.assertRaises(stage8.LegacyTwoServerStage8RuntimeRetiredError):
                stage8.run_command(command)

        self.assertFalse(run.called)

    def test_main_preflight_blocks_before_artifact_or_subprocess_access(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            artifact_dir = Path(tmp_dir) / "would-be-runtime-artifact"
            with patch.object(stage8.subprocess, "run") as run, patch(
                "sys.stdout", new_callable=io.StringIO
            ) as stdout:
                exit_code = stage8.main(["--mode", "preflight", "--artifact-dir", str(artifact_dir)])
            self.assertFalse(artifact_dir.exists())

        self.assertEqual(exit_code, 2)
        self.assertFalse(run.called)
        self.assertIn("blocked_legacy_two_server_stage8_runtime_retired", stdout.getvalue())

    def test_main_writes_plan_artifact_without_subprocess_execution(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            output = Path(tmp_dir) / "plan.json"
            with patch("sys.stdout", new_callable=io.StringIO):
                exit_code = stage8.main(
                    [
                        "--prefix",
                        "P8_STAGE_UNIT_",
                        "--artifact-dir",
                        tmp_dir,
                        "--output",
                        str(output),
                    ]
                )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["mode"], "plan")
        self.assertEqual(payload["status"], "planned")
        self.assertTrue(
            any("repair-drift-fixture/local-snapshot.json" in arg for arg in payload["preflight"]["commands"][-2]["args"])
        )


if __name__ == "__main__":
    unittest.main()
