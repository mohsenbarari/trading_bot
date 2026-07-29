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
    "IRAN_HOST": "65.109.220.59",
    "IRAN_SSH_PORT": "37067",
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
        with patch.dict(os.environ, {}, clear=True), patch.object(
            stage9, "resolve_deploy_settings", return_value=FAKE_SETTINGS
        ), patch.object(stage9, "run_git_value", side_effect=fake_git_value(branch)):
            return args, stage9.build_plan(args)

    def test_default_plan_is_non_mutating_until_guarded_sections(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            _args, plan = self.build_plan(Path(tmp_dir))

        self.assertEqual(plan["schema_version"], "sync_parity_stage9_production_rollout_v1")
        self.assertEqual(plan["status"], "planned")
        self.assertTrue(plan["branch_gate"]["planning_passed"])
        self.assertFalse(plan["branch_gate"]["release_passed"])
        self.assertEqual(plan["execution_contract"]["release_requires_branch"], "main")
        self.assertEqual(plan["execution_contract"]["ssh_strict_host_key_checking"], "accept-new")
        self.assertEqual(plan["transport_security_gate"]["status"], "passed")
        self.assertEqual(plan["read_only_preflight"]["status"], "blocked_until_explicit_confirm")
        self.assertEqual(plan["backup_confirmation"]["status"], "blocked_until_explicit_confirm")
        self.assertEqual(plan["release_plan"]["status"], "hard_disabled")
        self.assertTrue(plan["execution_contract"]["legacy_two_site_release_hard_disabled"])
        self.assertEqual(plan["strict_alert_enablement_plan"]["activation_gate"]["reason"], "missing_parity_evidence")

        self.assertTrue(all(not command["mutates_production"] for command in plan["read_only_preflight"]["commands"]))
        self.assertTrue(any(command["reads_production"] for command in plan["read_only_preflight"]["commands"]))
        self.assertTrue(all(command["mutates_production"] for command in plan["backup_confirmation"]["commands"]))
        self.assertTrue(all(command["mutates_production"] for command in plan["release_plan"]["commands"]))

        preflight_names = {command["name"] for command in plan["read_only_preflight"]["commands"]}
        self.assertIn("foreign_parity_snapshot_deep", preflight_names)
        self.assertIn("iran_parity_snapshot_deep", preflight_names)
        self.assertIn("production_predeploy_parity_compare_deep", preflight_names)
        self.assertIn("production_alerts_warning_only", preflight_names)

        local_gate = plan["local_release_gates"]["commands"][0]
        self.assertEqual(local_gate["args"][0], "env")
        self.assertIn("DATABASE_URL=postgresql+asyncpg://matrix_gate:matrix_gate@127.0.0.1:1/matrix_gate", local_gate["args"])
        self.assertIn("JWT_SECRET_KEY=matrix-gate-placeholder-jwt-secret-32-bytes", local_gate["args"])

    def test_iran_stage9_ssh_uses_accept_new_host_key_policy(self):
        ssh_command = stage9.iran_compose_exec(FAKE_SETTINGS, "python", "-V")

        self.assertEqual(ssh_command[0], "ssh")
        self.assertIn("StrictHostKeyChecking=accept-new", ssh_command)

    def test_release_plan_binds_the_selected_manifest_without_make_indirection(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            selected_manifest = Path(tmp_dir) / "selected-online.env"
            args = self.build_args(
                Path(tmp_dir),
                "--manifest",
                str(selected_manifest),
            )
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(stage9, "resolve_deploy_settings", return_value=FAKE_SETTINGS),
                patch.object(stage9, "run_git_value", side_effect=fake_git_value("main")),
            ):
                plan = stage9.build_plan(args)

        release = plan["release_plan"]["commands"]
        self.assertEqual(len(release), 1)
        self.assertEqual(
            release[0]["args"],
            [
                "bash",
                "./scripts/production_deploy_online.sh",
                "--manifest",
                str(selected_manifest),
                "release",
            ],
        )
        self.assertNotIn("make", release[0]["args"])

    def test_live_preflight_is_retired_before_transport_security_evaluation(self):
        insecure_settings = {**FAKE_SETTINGS, "SYNC_VERIFY_TLS": "false", "SYNC_CA_BUNDLE": ""}
        with tempfile.TemporaryDirectory() as tmp_dir:
            args = self.build_args(Path(tmp_dir), "--mode", "preflight")
            with patch.dict(os.environ, {}, clear=True), patch.object(
                stage9, "resolve_deploy_settings", return_value=insecure_settings
            ), patch.object(stage9, "run_git_value", side_effect=fake_git_value("main")):
                plan = stage9.build_plan(args)
                executed, exit_code = stage9.execute_plan(plan, args)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_legacy_two_site_live_mode_retired")
        self.assertEqual(executed["read_only_preflight"]["status"], "hard_disabled")

    def test_strict_alert_plan_uses_latest_parity_evidence_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            evidence = Path(tmp_dir) / "parity.json"
            evidence.write_text(
                json.dumps(
                    {
                        "summary": {
                            "status": "ok",
                            "fresh": True,
                            "mode": "deep",
                            "observed_at": "2026-06-28T05:00:00Z",
                            "business_drift_count": 0,
                            "critical_drift_count": 0,
                            "incomplete_count": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = self.build_args(Path(tmp_dir), "--latest-parity-status", str(evidence))

            plan = stage9.build_strict_alert_plan(args)

        self.assertEqual(plan["latest_parity_evidence"]["status"], "ok")
        self.assertEqual(plan["activation_gate"]["status"], "passed")

    def test_strict_alert_plan_can_require_artifact_backed_parity(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            evidence = Path(tmp_dir) / "parity.json"
            evidence.write_text(
                json.dumps(
                    {
                        "summary": {
                            "status": "ok",
                            "fresh": True,
                            "mode": "deep",
                            "observed_at": "2026-06-28T05:00:00Z",
                            "business_drift_count": 0,
                            "critical_drift_count": 0,
                            "incomplete_count": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = self.build_args(
                Path(tmp_dir),
                "--latest-parity-status",
                str(evidence),
                "--require-artifact-backed-parity",
            )

            plan = stage9.build_strict_alert_plan(args)

        self.assertTrue(plan["artifact_metadata_required"])
        self.assertEqual(plan["activation_gate"]["reason"], "missing_artifact_metadata")
        self.assertIn("comparison_artifact_hash", plan["activation_gate"]["missing_fields"])

    def test_strict_alert_plan_blocks_critical_parity_evidence(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            evidence = Path(tmp_dir) / "parity.json"
            evidence.write_text(
                json.dumps(
                    {
                        "summary": {
                            "status": "critical_drift",
                            "fresh": True,
                            "mode": "deep",
                            "observed_at": "2026-06-28T05:00:00Z",
                            "business_drift_count": 0,
                            "critical_drift_count": 1,
                            "incomplete_count": 0,
                        }
                    }
                ),
                encoding="utf-8",
            )
            args = self.build_args(Path(tmp_dir), "--latest-parity-status", str(evidence))

            plan = stage9.build_strict_alert_plan(args)

        self.assertEqual(plan["activation_gate"]["reason"], "parity_status_critical_drift")

    def test_preflight_is_retired_even_without_or_with_a_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            args, plan = self.build_plan(Path(tmp_dir), mode="preflight")

        with patch.dict(os.environ, {}, clear=True):
            executed, exit_code = stage9.execute_plan(plan, args)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_legacy_two_site_live_mode_retired")
        self.assertEqual(executed["read_only_preflight"]["status"], "hard_disabled")

    def test_backup_and_release_modes_are_hard_disabled_before_confirmation_checks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            backup_args, backup_plan = self.build_plan(Path(tmp_dir), mode="backup")
            release_args, release_plan = self.build_plan(Path(tmp_dir), branch="main", mode="execute")

        with patch.dict(os.environ, {}, clear=True):
            backup_executed, backup_exit = stage9.execute_plan(backup_plan, backup_args)
            release_executed, release_exit = stage9.execute_plan(release_plan, release_args)

        self.assertEqual(backup_exit, 2)
        self.assertEqual(backup_executed["status"], "blocked_legacy_two_site_live_mode_retired")
        self.assertEqual(release_exit, 2)
        self.assertEqual(release_executed["status"], "blocked_legacy_two_site_live_mode_retired")

        with patch.dict(
            os.environ,
            {
                stage9.PREFLIGHT_CONFIRM_ENV: stage9.PREFLIGHT_CONFIRM_VALUE,
                stage9.BACKUP_CONFIRM_ENV: stage9.BACKUP_CONFIRM_VALUE,
                stage9.RELEASE_CONFIRM_ENV: stage9.RELEASE_CONFIRM_VALUE,
            },
            clear=True,
        ), patch.object(
            stage9,
            "run_command",
            side_effect=AssertionError("retired release must not execute any section"),
        ):
            release_executed, release_exit = stage9.execute_plan(release_plan, release_args)

        self.assertEqual(release_exit, 2)
        self.assertEqual(release_executed["status"], "blocked_legacy_two_site_live_mode_retired")
        self.assertEqual(
            release_executed["release_plan"]["reason"],
            stage9.LEGACY_RELEASE_RETIREMENT_REASON,
        )

    def test_release_execution_is_hard_disabled_before_branch_or_confirmation_checks(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            args, plan = self.build_plan(Path(tmp_dir), mode="execute")

        with patch.dict(os.environ, {stage9.RELEASE_CONFIRM_ENV: stage9.RELEASE_CONFIRM_VALUE}, clear=True):
            executed, exit_code = stage9.execute_plan(plan, args)

        self.assertEqual(exit_code, 2)
        self.assertEqual(executed["status"], "blocked_legacy_two_site_live_mode_retired")

    def test_every_live_cli_mode_denies_before_plan_or_command_construction(self):
        for mode in ("preflight", "backup", "execute", "postdeploy"):
            with self.subTest(mode=mode), patch.dict(
                os.environ,
                {
                    stage9.PREFLIGHT_CONFIRM_ENV: stage9.PREFLIGHT_CONFIRM_VALUE,
                    stage9.BACKUP_CONFIRM_ENV: stage9.BACKUP_CONFIRM_VALUE,
                    stage9.RELEASE_CONFIRM_ENV: stage9.RELEASE_CONFIRM_VALUE,
                },
                clear=True,
            ), patch.object(
                stage9,
                "build_plan",
                side_effect=AssertionError("retired mode reached build_plan"),
            ), patch.object(
                stage9,
                "run_command",
                side_effect=AssertionError("retired mode reached run_command"),
            ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = stage9.main(["--mode", mode])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(payload["status"], "blocked_legacy_two_site_live_mode_retired")
            self.assertEqual(payload["mode"], mode)

    def test_direct_command_helper_denies_a_planned_production_command(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            _args, plan = self.build_plan(Path(tmp_dir), mode="preflight")
        production_command = plan["read_only_preflight"]["commands"][0]

        with patch.object(
            stage9.subprocess,
            "run",
            side_effect=AssertionError("retired command reached subprocess.run"),
        ), self.assertRaisesRegex(stage9.LegacyTwoSiteStage9RuntimeRetiredError, "hard-disabled"):
            stage9.run_command(production_command)

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
