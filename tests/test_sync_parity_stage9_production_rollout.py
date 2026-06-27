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
MODULE_PATH = REPO_ROOT / "scripts" / "run_sync_parity_stage9_production_rollout.py"

spec = importlib.util.spec_from_file_location("run_sync_parity_stage9_production_rollout", MODULE_PATH)
stage9 = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = stage9
spec.loader.exec_module(stage9)


FAKE_SETTINGS = {
    "DEPLOY_MANIFEST": "./deploy/production/online.env",
    "IRAN_HOST": "87.107.3.22",
    "IRAN_SSH_PORT": "22",
    "IRAN_SSH_USER": "root",
    "IRAN_PROJECT_DIR": "/srv/trading-bot/current",
}


def fake_git_value(branch: str):
    def inner(args):
        if args == ["branch", "--show-current"]:
            return branch
        if args == ["rev-parse", "HEAD"]:
            return "abc123"
        return None

    return inner


class SyncParityStage9ProductionRolloutTests(unittest.TestCase):
    def build_args(self, artifact_dir: Path, *extra: str):
        return stage9.parse_args(
            [
                "--stamp",
                "20260627T190000Z",
                "--artifact-dir",
                str(artifact_dir),
                *extra,
            ]
        )

    def build_plan(self, artifact_dir: Path, *, branch: str = "candidate/sync-parity-hardening", mode: str = "plan"):
        args = self.build_args(artifact_dir, "--mode", mode)
        with patch.object(stage9, "resolve_deploy_settings", return_value=FAKE_SETTINGS), patch.object(
            stage9, "run_git_value", side_effect=fake_git_value(branch)
        ):
            return args, stage9.build_plan(args)

    def test_default_plan_is_non_mutating_until_guarded_sections(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            _args, plan = self.build_plan(Path(tmp_dir))

        self.assertEqual(plan["schema_version"], "sync_parity_stage9_production_rollout_v1")
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["branch_gate"]["planning_passed"])
        self.assertFalse(plan["branch_gate"]["release_passed"])
        self.assertEqual(plan["execution_contract"]["release_requires_branch"], "main")
        self.assertEqual(plan["read_only_preflight"]["status"], "blocked_until_explicit_confirm")
        self.assertEqual(plan["backup_confirmation"]["status"], "blocked_until_explicit_confirm")
        self.assertEqual(plan["release_plan"]["status"], "blocked_until_main_and_explicit_confirm")

        self.assertTrue(all(not command["mutates_production"] for command in plan["read_only_preflight"]["commands"]))
        self.assertTrue(any(command["reads_production"] for command in plan["read_only_preflight"]["commands"]))
        self.assertTrue(all(command["mutates_production"] for command in plan["backup_confirmation"]["commands"]))
        self.assertTrue(all(command["mutates_production"] for command in plan["release_plan"]["commands"]))

        preflight_names = {command["name"] for command in plan["read_only_preflight"]["commands"]}
        self.assertIn("foreign_parity_snapshot_deep", preflight_names)
        self.assertIn("iran_parity_snapshot_deep", preflight_names)
        self.assertIn("production_predeploy_parity_compare_deep", preflight_names)
        self.assertIn("production_alerts_warning_only", preflight_names)

    def test_preflight_requires_explicit_read_only_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            args, plan = self.build_plan(Path(tmp_dir), mode="preflight")

        with patch.dict(os.environ, {}, clear=True):
            executed, exit_code = stage9.execute_plan(plan, args)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_preflight_confirmation_missing")
        self.assertEqual(executed["read_only_preflight"]["status"], "blocked_confirmation_missing")

    def test_backup_and_release_have_separate_confirmations(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_args, backup_plan = self.build_plan(Path(tmp_dir), mode="backup")
            release_args, release_plan = self.build_plan(Path(tmp_dir), branch="main", mode="execute")

        with patch.dict(os.environ, {}, clear=True):
            backup_executed, backup_exit = stage9.execute_plan(backup_plan, backup_args)
            release_executed, release_exit = stage9.execute_plan(release_plan, release_args)

        self.assertEqual(backup_exit, 2)
        self.assertEqual(backup_executed["status"], "blocked_backup_confirmation_missing")
        self.assertEqual(release_exit, 2)
        self.assertEqual(release_executed["status"], "blocked_preflight_confirmation_missing")

        with patch.dict(
            os.environ,
            {
                stage9.PREFLIGHT_CONFIRM_ENV: stage9.PREFLIGHT_CONFIRM_VALUE,
                stage9.BACKUP_CONFIRM_ENV: stage9.BACKUP_CONFIRM_VALUE,
            },
            clear=True,
        ):
            release_executed, release_exit = stage9.execute_plan(release_plan, release_args)

        self.assertEqual(release_exit, 2)
        self.assertEqual(release_executed["status"], "blocked_release_confirmation_missing")

    def test_release_execution_is_blocked_on_candidate_branch_even_with_confirm(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            args, plan = self.build_plan(Path(tmp_dir), mode="execute")

        with patch.dict(os.environ, {stage9.RELEASE_CONFIRM_ENV: stage9.RELEASE_CONFIRM_VALUE}, clear=True):
            executed, exit_code = stage9.execute_plan(plan, args)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_release_requires_main")

    def test_local_gates_mode_runs_only_local_commands(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            args, plan = self.build_plan(Path(tmp_dir), mode="local-gates")

        seen = []

        def fake_run(command):
            seen.append(command["name"])
            self.assertFalse(command["reads_production"])
            self.assertFalse(command["mutates_production"])
            return {"name": command["name"], "status": "passed", "returncode": 0}

        with patch.object(stage9, "run_command", side_effect=fake_run):
            executed, exit_code = stage9.execute_plan(plan, args)

        self.assertEqual(exit_code, 0)
        self.assertEqual(executed["status"], "passed")
        self.assertEqual(seen, [command["name"] for command in plan["local_release_gates"]["commands"]])

    def test_main_writes_plan_without_touching_production(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.object(
            stage9, "resolve_deploy_settings", return_value=FAKE_SETTINGS
        ), patch.object(stage9, "run_git_value", side_effect=fake_git_value("candidate/sync-parity-hardening")):
            output = Path(tmp_dir) / "plan.json"
            with patch("sys.stdout", new_callable=io.StringIO):
                exit_code = stage9.main(
                    [
                        "--stamp",
                        "20260627T190000Z",
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
        self.assertEqual(payload["branch_gate"]["actual_branch"], "candidate/sync-parity-hardening")


if __name__ == "__main__":
    unittest.main()
