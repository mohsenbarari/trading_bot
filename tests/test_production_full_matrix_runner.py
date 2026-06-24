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

    def test_execution_plan_builds_unsupported_policy_probe_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_base_trade_shape",
                "--policy",
                "unsupported",
                "--manifest-id",
                "PBTS-0163",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "unsupported_policy_negative_probe")
        self.assertEqual(scenario_plan["unsupported_reasons"], ["tier2_cannot_use_telegram_request"])
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("run-unsupported-policy-case", rendered)
        self.assertIn("--unsupported-reason", rendered)
        self.assertIn("tier2_cannot_use_telegram_request", rendered)

    def test_execution_plan_builds_market_behavior_probe_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "market_behavior",
                "--manifest-id",
                "MB-CLM-001",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "market_behavior_comprehensive_probe")
        self.assertEqual(scenario_plan["server"], "foreign")
        self.assertEqual(scenario_plan["source_scenario_id"], "CLM-001")
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("run_bot_webapp_comprehensive_load_matrix.py", rendered)
        self.assertIn("--allow-production-execution", rendered)
        self.assertIn("PRODUCTION_TEST_CLEANUP_CONFIRM", rendered)

    def test_execution_plan_builds_delivery_contract_catalog_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "delivery_contract",
                "--manifest-id",
                "DC-TDN-004",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "delivery_contract_catalog_assertion")
        self.assertEqual(scenario_plan["source_scenario_id"], "TDN-004")
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("report_trade_notification_delivery_matrix.py", rendered)
        self.assertIn("--scenario", rendered)
        self.assertIn("TDN-004", rendered)
        self.assertTrue(all(command["mutates_production"] is False for command in scenario_plan["commands"]))

    def test_execution_plan_builds_targeted_join_probe_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "targeted_trade_delivery_join",
                "--manifest-id",
                "TJ-TDN-004",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "targeted_trade_delivery_join_probe")
        self.assertEqual(scenario_plan["server"], "iran")
        self.assertEqual(scenario_plan["source_scenario_id"], "TDN-004")
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("run_trade_delivery_targeted_join_matrix.py", rendered)
        self.assertIn("--allow-production-execution", rendered)
        self.assertIn("--allow-production-cleanup", rendered)
        self.assertIn("PRODUCTION_TEST_CLEANUP_CONFIRM", rendered)
        self.assertIn("cleanup_targeted_trade_delivery_join_scenario", rendered)

    def test_execution_plan_builds_outage_policy_composed_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_base_trade_shape",
                "--manifest-id",
                "PBTS-0007",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "outage_policy_composed_probe")
        self.assertEqual(scenario_plan["outage_id"], "short_under_2m")
        self.assertEqual(scenario_plan["delivery_scenario_id"], "TDN-002")
        self.assertEqual(scenario_plan["trade_correctness_driver"], "two_server_dual_role_hot_offer")
        self.assertEqual(scenario_plan["outage_delivery_policy_driver"], "targeted_trade_delivery_join_probe")
        self.assertNotEqual(scenario_plan["scenario_prefix"], scenario_plan["outage_delivery_policy_prefix"])
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("run-role-plan", rendered)
        self.assertIn("run_trade_delivery_targeted_join_matrix.py", rendered)
        self.assertIn("TDN-002", rendered)

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

    def test_execution_plan_builds_customer_actor_composed_commands(self):
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
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "customer_accountant_actor_composed_probe")
        self.assertEqual(scenario_plan["actor_pair_id"], "user__tier1_same_owner")
        self.assertEqual(scenario_plan["delivery_scenario_id"], "TDN-013")
        self.assertEqual(scenario_plan["actor_policy_driver"], "targeted_trade_delivery_join_probe")
        self.assertEqual(scenario_plan["shape_stress_driver"], "two_server_dual_role_hot_offer")
        self.assertNotEqual(scenario_plan["actor_policy_prefix"], scenario_plan["shape_stress_prefix"])
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("run_trade_delivery_targeted_join_matrix.py", rendered)
        self.assertIn("run-role-plan", rendered)

    def test_execution_plan_summarizes_whole_manifest_driver_gaps(self):
        plan = runner.build_plan(self.build_args("--mode", "execution-plan"))
        execution_plan = plan["execution_plan"]
        summary = execution_plan["driver_gap_summary"]

        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 5555)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        self.assertEqual(summary["total"], 0)
        self.assertEqual(summary["by_section"], {})
        self.assertEqual(summary["by_driver_gap_bucket"], {})
        self.assertEqual(
            [
                (item["bucket"], item["remaining_gap_count"])
                for item in execution_plan["driver_gap_roadmap"]
            ],
            [],
        )

    def test_execution_plan_full_coverage_gate_passes_for_whole_manifest(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "execution-plan.json"
            with patch("sys.stdout", new_callable=io.StringIO) as stdout:
                exit_code = runner.main(
                    [
                        "--prefix",
                        "PFM_20260624_180000_",
                        "--mode",
                        "execution-plan",
                        "--require-full-driver-coverage",
                        "--output",
                        str(output),
                    ]
                )

            full_payload = json.loads(output.read_text(encoding="utf-8"))
            stdout_payload = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout_payload["status"], "execution_plan_built")
        self.assertEqual(full_payload["status"], "execution_plan_built")
        self.assertTrue(full_payload["execution_plan"]["coverage_gate"]["passed"])
        self.assertEqual(full_payload["execution_plan"]["driver_gap_count"], 0)

    def test_execution_plan_full_coverage_gate_passes_for_filtered_executable_scope(self):
        with patch("sys.stdout", new_callable=io.StringIO) as stdout:
            exit_code = runner.main(
                [
                    "--prefix",
                    "PFM_20260624_180000_",
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
                    "--require-full-driver-coverage",
                ]
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "execution_plan_built")
        self.assertEqual(payload["execution_plan"]["driver_gap_count"], 0)
        self.assertTrue(payload["execution_plan"]["coverage_gate"]["passed"])

    def test_execution_plan_builds_negative_guard_commands_for_implemented_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-001",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "own_offer_request")
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("run-negative-guard-case", rendered)
        self.assertIn("--skip-initial-cleanup", rendered)
        self.assertIn("TRADING_BOT_SERVICE=load_runner", rendered)
        self.assertIn("BOT_TOKEN=", rendered)

    def test_execution_plan_builds_negative_guard_commands_for_market_closed_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-007",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "market_closed")

    def test_execution_plan_builds_negative_guard_commands_for_inactive_offer_owner_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-008",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "inactive_offer_owner")

    def test_execution_plan_builds_negative_guard_commands_for_tier2_offer_creation_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-012",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "tier2_offer_creation")

    def test_execution_plan_builds_negative_guard_commands_for_tier2_telegram_request_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-013",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "tier2_telegram_request")

    def test_execution_plan_builds_negative_guard_commands_for_limit_cases(self):
        for manifest_id, case_id in (
            ("NBG-015", "daily_trade_limit_exceeded"),
            ("NBG-016", "daily_request_limit_exceeded"),
            ("NBG-017", "active_commodity_limit_exceeded"),
        ):
            with self.subTest(manifest_id=manifest_id):
                plan = runner.build_plan(
                    self.build_args(
                        "--mode",
                        "execution-plan",
                        "--section",
                        "negative_business_guard",
                        "--manifest-id",
                        manifest_id,
                        "--require-full-driver-coverage",
                    )
                )

                execution_plan = plan["execution_plan"]
                self.assertEqual(plan["status"], "execution_plan_built")
                self.assertEqual(execution_plan["executable_count"], 1)
                self.assertEqual(execution_plan["driver_gap_count"], 0)
                scenario_plan = execution_plan["scenario_plans"][0]
                self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
                self.assertEqual(scenario_plan["case_id"], case_id)

    def test_execution_plan_builds_negative_guard_commands_for_remote_authority_unavailable_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-018",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "remote_authority_unavailable")

    def test_execution_plan_builds_negative_guard_commands_for_internal_authority_cases(self):
        for manifest_id, case_id in (
            ("NBG-019", "bad_internal_signature"),
            ("NBG-020", "wrong_authoritative_server"),
        ):
            with self.subTest(manifest_id=manifest_id):
                plan = runner.build_plan(
                    self.build_args(
                        "--mode",
                        "execution-plan",
                        "--section",
                        "negative_business_guard",
                        "--manifest-id",
                        manifest_id,
                        "--require-full-driver-coverage",
                    )
                )

                execution_plan = plan["execution_plan"]
                self.assertEqual(plan["status"], "execution_plan_built")
                self.assertEqual(execution_plan["executable_count"], 1)
                self.assertEqual(execution_plan["driver_gap_count"], 0)
                scenario_plan = execution_plan["scenario_plans"][0]
                self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
                self.assertEqual(scenario_plan["case_id"], case_id)

    def test_execution_plan_builds_duplicate_replay_commands_for_webapp_request_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_stress_overlay",
                "--manifest-id",
                "PO-1082",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "two_server_dual_role_hot_offer")
        self.assertEqual(scenario_plan["idempotency_mode"], "duplicate_replay")
        self.assertEqual(scenario_plan["request_surface"], "webapp")
        self.assertEqual(scenario_plan["total_requests"], 2)
        self.assertEqual(scenario_plan["expected_winner_count"], 1)
        self.assertEqual(scenario_plan["expected_remaining_quantity"], 10)
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("--scenario-name", rendered)
        self.assertIn("duplicate_idempotency_replay", rendered)
        self.assertIn("--idempotency-mode", rendered)
        self.assertIn("duplicate_replay", rendered)
        self.assertIn("--allow-nonterminal-offer", rendered)

    def test_execution_plan_builds_duplicate_replay_commands_for_telegram_request_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_stress_overlay",
                "--manifest-id",
                "PO-1099",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["idempotency_mode"], "duplicate_replay")
        self.assertEqual(scenario_plan["request_surface"], "telegram")
        self.assertEqual(scenario_plan["total_requests"], 2)
        self.assertEqual(scenario_plan["expected_winner_count"], 1)
        self.assertEqual(scenario_plan["expected_remaining_quantity"], 0)
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("--request-surface", rendered)
        self.assertIn("telegram", rendered)
        self.assertIn("duplicate_replay", rendered)

    def test_execution_plan_builds_manual_expire_trade_race_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_stress_overlay",
                "--manifest-id",
                "PO-1729",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        groups_by_name = {group["name"]: group for group in scenario_plan["execution_groups"]}
        command_names = [command["name"] for command in scenario_plan["commands"]]
        self.assertEqual(scenario_plan["request_surface"], "webapp")
        self.assertEqual(scenario_plan["idempotency_mode"], "unique")
        self.assertEqual(scenario_plan["total_requests"], 8)
        self.assertIn("run_manual_expiry_race_on_offer_home_server", command_names)
        self.assertEqual(len(groups_by_name["role_workers"]["commands"]), 3)
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("manual_expire_trade_race", rendered)
        self.assertIn("run-manual-expiry-race", rendered)
        self.assertIn("--manual-expiry-result", rendered)

    def test_execution_plan_builds_time_expire_trade_race_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_stress_overlay",
                "--manifest-id",
                "PO-2377",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        groups_by_name = {group["name"]: group for group in scenario_plan["execution_groups"]}
        command_names = [command["name"] for command in scenario_plan["commands"]]
        self.assertEqual(scenario_plan["request_surface"], "webapp")
        self.assertEqual(scenario_plan["idempotency_mode"], "unique")
        self.assertEqual(scenario_plan["total_requests"], 8)
        self.assertIn("run_time_expiry_race_on_offer_home_server", command_names)
        self.assertEqual(len(groups_by_name["role_workers"]["commands"]), 3)
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("time_expire_trade_race", rendered)
        self.assertIn("run-time-expiry-race", rendered)
        self.assertIn("--time-expiry-result", rendered)

    def test_execution_plan_builds_read_during_write_commands(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "production_stress_overlay",
                "--manifest-id",
                "PO-3025",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        groups_by_name = {group["name"]: group for group in scenario_plan["execution_groups"]}
        command_names = [command["name"] for command in scenario_plan["commands"]]
        self.assertEqual(scenario_plan["request_surface"], "webapp")
        self.assertEqual(scenario_plan["total_requests"], 12)
        self.assertIn("distribute_prepare_to_foreign", command_names)
        self.assertIn("distribute_prepare_to_iran", command_names)
        self.assertIn("run_read_during_write_telegram_foreign", command_names)
        self.assertIn("run_read_during_write_webapp_iran", command_names)
        self.assertEqual(len(groups_by_name["role_workers"]["commands"]), 4)
        rendered = json.dumps(scenario_plan, ensure_ascii=False)
        self.assertIn("read_during_write", rendered)
        self.assertIn("run-read-during-write", rendered)
        self.assertIn("--read-during-write-result", rendered)

    def test_execution_plan_builds_negative_guard_commands_for_pre_ledger_case(self):
        plan = runner.build_plan(
            self.build_args(
                "--mode",
                "execution-plan",
                "--section",
                "negative_business_guard",
                "--manifest-id",
                "NBG-010",
                "--require-full-driver-coverage",
            )
        )

        execution_plan = plan["execution_plan"]
        self.assertEqual(plan["status"], "execution_plan_built")
        self.assertEqual(execution_plan["executable_count"], 1)
        self.assertEqual(execution_plan["driver_gap_count"], 0)
        scenario_plan = execution_plan["scenario_plans"][0]
        self.assertEqual(scenario_plan["driver"], "negative_guard_webapp_iran_probe")
        self.assertEqual(scenario_plan["case_id"], "watch_role_market_action")

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
