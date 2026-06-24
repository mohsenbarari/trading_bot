import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_production_full_matrix.py"

spec = importlib.util.spec_from_file_location("run_production_full_matrix", MODULE_PATH)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


class ProductionFullMatrixRunnerTests(unittest.TestCase):
    def build_args(self, *extra: str):
        return runner.parse_args(["--prefix", "PFM_20260624_180000_", *extra])

    def test_default_plan_selects_entire_manifest_without_mutating_production(self):
        plan = runner.build_plan(self.build_args())

        self.assertEqual(plan["schema_version"], "production_full_matrix_runner_plan_v1")
        self.assertFalse(plan["mutates_production"])
        self.assertFalse(plan["execute_requested"])
        self.assertEqual(plan["status"], "planned")
        self.assertEqual(plan["selected_summary"]["selected_count"], 5555)
        self.assertFalse(plan["execution_contract"]["production_drivers_implemented"])

    def test_filters_supported_base_trade_shape_scenarios(self):
        plan = runner.build_plan(
            self.build_args(
                "--section",
                "production_base_trade_shape",
                "--policy",
                "supported",
                "--surface-pair",
                "webapp_offer__webapp_request",
                "--outage-id",
                "stable",
                "--offer-type",
                "buy",
                "--shape",
                "wholesale_full",
            )
        )

        self.assertEqual(plan["selected_summary"]["selected_count"], 11)
        self.assertEqual(plan["selected_summary"]["by_policy"], {"supported": 11})
        self.assertEqual(plan["selected_summary"]["by_quadrant"], {"iran_to_iran": 11})
        self.assertTrue(all(step["policy_supported"] is True for step in plan["steps"]))

    def test_filters_unsupported_policy_scenarios_as_negative_evidence(self):
        plan = runner.build_plan(
            self.build_args(
                "--section",
                "production_base_trade_shape",
                "--policy",
                "unsupported",
                "--manifest-id",
                "PBTS-0163",
            )
        )

        self.assertEqual(plan["selected_summary"]["selected_count"], 1)
        self.assertEqual(plan["steps"][0]["policy_supported"], False)
        self.assertIn("no_partial_mutation_on_reject", plan["steps"][0]["assertion_refs"])

    def test_sharding_is_deterministic_and_non_overlapping(self):
        first = runner.build_plan(
            self.build_args("--section", "production_base_trade_shape", "--shard-count", "2", "--shard-index", "1")
        )
        second = runner.build_plan(
            self.build_args("--section", "production_base_trade_shape", "--shard-count", "2", "--shard-index", "2")
        )
        first_ids = {step["manifest_id"] for step in first["steps"]}
        second_ids = {step["manifest_id"] for step in second["steps"]}

        self.assertEqual(len(first_ids), 612)
        self.assertEqual(len(second_ids), 612)
        self.assertFalse(first_ids.intersection(second_ids))

    def test_execute_flag_fails_closed(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = runner.main(
                [
                    "--prefix",
                    "PFM_20260624_180000_",
                    "--section",
                    "production_base_trade_shape",
                    "--max-scenarios",
                    "1",
                    "--execute",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "blocked_not_implemented")
        self.assertTrue(payload["execute_requested"])

    def test_preflight_mode_builds_non_mutating_command_plan(self):
        plan = runner.build_plan(self.build_args("--mode", "preflight"))

        self.assertEqual(plan["status"], "preflight_planned")
        self.assertTrue(plan["execution_contract"]["preflight_driver_implemented"])
        self.assertFalse(plan["execution_contract"]["production_drivers_implemented"])
        self.assertGreaterEqual(len(plan["preflight"]["commands"]), 8)
        self.assertTrue(all(not item["mutates_production"] for item in plan["preflight"]["commands"]))

    def test_execution_plan_builds_two_server_dual_role_commands_for_user_stable_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_base_trade_shape",
                "--policy",
                "supported",
                "--actor-pair-id",
                "user__user",
                "--outage-id",
                "stable",
                "--surface-pair",
                "webapp_offer__telegram_request",
                "--offer-type",
                "sell",
                "--shape",
                "retail_two_lot",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(plan["selected_summary"]["selected_count"], 1)
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)

        scenario_plan = execution_plan["scenario_plans"][0]
        command_names = [command["name"] for command in scenario_plan["commands"]]
        groups_by_name = {group["name"]: group for group in scenario_plan["execution_groups"]}
        self.assertEqual(scenario_plan["offer_home_server"], "iran")
        self.assertIn("prepare_on_offer_home_server", command_names)
        self.assertIn("distribute_telegram_role_plan", command_names)
        self.assertIn("run_role_telegram_foreign", command_names)
        self.assertIn("run_role_webapp_iran", command_names)
        self.assertIn("collect_telegram_role_result", command_names)
        self.assertIn("finalize_on_offer_home_server", command_names)
        self.assertEqual(groups_by_name["role_workers"]["mode"], "concurrent")
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("--patch-external-side-effects", rendered)
        self.assertIn("--allow-production-execution", rendered)
        self.assertIn("PRODUCTION_FULL_MATRIX_CONFIRM", rendered)
        self.assertIn("--retail", rendered)

    def test_execution_plan_keeps_customer_cases_as_explicit_driver_gaps(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_base_trade_shape",
                "--policy",
                "supported",
                "--actor-pair-id",
                "user__tier1_same_owner",
                "--outage-id",
                "stable",
                "--max-scenarios",
                "1",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(execution_plan["executable_count"], 0)
        self.assertEqual(execution_plan["driver_gap_count"], 1)
        self.assertEqual(
            execution_plan["driver_gaps"][0]["driver_gap"],
            "dual_role_worker_currently_supports_standard_user_to_standard_user_only",
        )

    def test_preflight_execute_requires_separate_confirmation(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = runner.main(
                [
                    "--prefix",
                    "PFM_20260624_180000_",
                    "--mode",
                    "preflight",
                    "--execute",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(payload["status"], "blocked_preflight_confirmation_missing")
        self.assertEqual(payload["preflight"]["status"], "blocked_confirmation_missing")

    def test_preflight_execute_runs_non_mutating_commands_when_confirmed(self):
        def fake_run(args, **_kwargs):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "preflight.json"
            with patch.dict(
                os.environ,
                {runner.PREFLIGHT_CONFIRM_ENV: runner.PREFLIGHT_CONFIRM_VALUE},
                clear=True,
            ), patch.object(runner.subprocess, "run", side_effect=fake_run) as run_mock, patch(
                "sys.stdout", new_callable=io.StringIO
            ) as stdout:
                exit_code = runner.main(
                    [
                        "--prefix",
                        "PFM_20260624_180000_",
                        "--mode",
                        "preflight",
                        "--execute",
                        "--output",
                        str(output),
                    ]
                )

            full_payload = json.loads(output.read_text(encoding="utf-8"))
            stdout_payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout_payload["status"], "preflight_passed")
        self.assertEqual(full_payload["preflight"]["status"], "preflight_passed")
        self.assertEqual(run_mock.call_count, len(full_payload["preflight"]["commands"]))
        self.assertTrue(all(item["status"] == "passed" for item in full_payload["preflight"]["results"]))

    def test_cli_writes_output_and_prints_compact_summary(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "run-plan.json"
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = runner.main(
                    [
                        "--prefix",
                        "PFM_20260624_180000_",
                        "--section",
                        "negative_business_guard",
                        "--output",
                        str(output),
                    ]
                )

            self.assertEqual(exit_code, 0)
            full_payload = json.loads(output.read_text(encoding="utf-8"))
            stdout_payload = json.loads(stdout.getvalue())

        self.assertEqual(full_payload["selected_summary"]["selected_count"], 23)
        self.assertEqual(stdout_payload["selected_summary"]["selected_count"], 23)
        self.assertNotIn("steps", stdout_payload)


if __name__ == "__main__":
    unittest.main()
