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

    def test_default_plan_is_non_mutating_and_keeps_execution_blocked(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            plan = stage8.build_plan(self.build_args(Path(tmp_dir)))

        self.assertEqual(plan["schema_version"], "sync_parity_stage8_staging_rollout_v1")
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["branch_gate"]["passed"])
        self.assertFalse(plan["execute_requested"])
        self.assertFalse(plan["execution_contract"]["production_deploy_allowed"])
        self.assertEqual(plan["execution_plan"]["status"], "blocked_until_explicit_confirm")
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

    def test_execute_mode_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            args = self.build_args(Path(tmp_dir), "--mode", "execute")
            plan = stage8.build_plan(args)

        with patch.dict(os.environ, {}, clear=True):
            executed, exit_code = stage8.execute_plan(plan, include_mutating=True)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_execution_confirmation_missing")
        self.assertEqual(executed["execution_plan"]["status"], "blocked_confirmation_missing")

    def test_preflight_mode_runs_only_non_mutating_commands(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(stage8, "run_git_value", side_effect=fake_git_value):
            args = self.build_args(Path(tmp_dir), "--mode", "preflight")
            plan = stage8.build_plan(args)

        seen_names = []

        def fake_run(command):
            seen_names.append(command["name"])
            self.assertFalse(command["mutates_staging"])
            return {"name": command["name"], "status": "passed", "returncode": 0}

        with patch.object(stage8, "run_command", side_effect=fake_run):
            executed, exit_code = stage8.execute_plan(plan, include_mutating=False)

        self.assertEqual(exit_code, 0)
        self.assertEqual(executed["status"], "passed")
        self.assertEqual(seen_names, [command["name"] for command in plan["preflight"]["commands"]])
        self.assertNotIn("staging_candidate_full_matrix_no_pressure", seen_names)

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
