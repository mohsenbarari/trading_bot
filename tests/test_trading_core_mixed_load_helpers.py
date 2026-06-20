import asyncio
import unittest
from unittest.mock import patch

from aiogram.methods import AnswerCallbackQuery

from scripts import trading_core_probe_worker as worker


class TradingCoreMixedLoadHelperTests(unittest.TestCase):
    def test_build_mixed_surface_plan_preserves_ratio_and_skips_owner(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]

        plan = worker.build_mixed_surface_plan(
            users=users,
            owner_user_id=1,
            total_requests=20,
            telegram_ratio=0.6,
        )

        self.assertEqual(len(plan), 20)
        self.assertEqual(sum(1 for item in plan if item.surface == "telegram"), 12)
        self.assertEqual(sum(1 for item in plan if item.surface == "webapp"), 8)
        self.assertNotIn(1, {item.user_id for item in plan})

    def test_build_mixed_surface_plan_rejects_invalid_inputs(self):
        users = [worker.LoadUserRef(user_id=1, telegram_id=9001)]

        with self.assertRaises(worker.TradingProbeError):
            worker.build_mixed_surface_plan(
                users=users,
                owner_user_id=1,
                total_requests=10,
                telegram_ratio=0.6,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.build_mixed_surface_plan(
                users=[worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)],
                owner_user_id=1,
                total_requests=0,
                telegram_ratio=0.6,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.build_mixed_surface_plan(
                users=[worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)],
                owner_user_id=1,
                total_requests=10,
                telegram_ratio=1.0,
            )

    def test_summarize_attempt_results_reports_business_and_telegram_rps(self):
        results = [
            worker.MixedLoadAttemptResult(index=0, surface="telegram", status="success", duration_ms=10),
            worker.MixedLoadAttemptResult(index=1, surface="telegram", status="rejected", duration_ms=20),
            worker.MixedLoadAttemptResult(index=2, surface="webapp", status="rejected", duration_ms=30),
            worker.MixedLoadAttemptResult(index=3, surface="webapp", status="error", duration_ms=40),
        ]

        summary = worker.summarize_attempt_results(results, elapsed_seconds=2.0)

        self.assertEqual(summary["business_request_rps"], 2.0)
        self.assertEqual(summary["telegram_update_count"], 4)
        self.assertEqual(summary["telegram_update_rps"], 2.0)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["rejected"], 2)
        self.assertEqual(summary["error"], 1)
        self.assertEqual(summary["surfaces"]["telegram"]["total"], 2)
        self.assertEqual(summary["surfaces"]["webapp"]["total"], 2)

    def test_dual_role_worker_plans_split_distribution_and_share_barrier(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]

        plans = worker.build_dual_role_worker_plans(
            run_id="run-1",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=20,
            telegram_ratio=0.6,
            target_rps=600.0,
            amount=5,
            barrier_epoch=1234.5,
        )

        self.assertEqual(set(plans), {"telegram_foreign", "webapp_iran"})
        telegram_plan = worker.validate_role_plan_artifact(plans["telegram_foreign"])
        webapp_plan = worker.validate_role_plan_artifact(plans["webapp_iran"])
        self.assertEqual(telegram_plan["surface"], "telegram")
        self.assertEqual(webapp_plan["surface"], "webapp")
        self.assertEqual(len(telegram_plan["attempts"]), 12)
        self.assertEqual(len(webapp_plan["attempts"]), 8)
        self.assertEqual(telegram_plan["barrier_epoch"], webapp_plan["barrier_epoch"])
        self.assertEqual(worker.assert_role_plan_barrier_skew(plans, max_skew_seconds=0.001)["observed_skew_seconds"], 0.0)

    def test_role_plan_artifact_validation_fails_closed(self):
        users = [worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-2",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id=None,
            total_requests=2,
            telegram_ratio=0.5,
            target_rps=10.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        broken = dict(plans["telegram_foreign"])
        broken["surface"] = "webapp"

        with self.assertRaises(worker.TradingProbeError):
            worker.validate_role_plan_artifact(broken)

    def test_dry_role_results_merge_into_required_artifact_schema(self):
        users = [worker.LoadUserRef(user_id=index, telegram_id=9000 + index) for index in range(1, 12)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-3",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=10,
            telegram_ratio=0.6,
            target_rps=100.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        telegram_result = worker.build_dry_role_result_artifact(plans["telegram_foreign"], started_epoch=2000.0)
        webapp_result = worker.build_dry_role_result_artifact(plans["webapp_iran"], started_epoch=2000.001)

        merged = worker.merge_role_result_artifacts([telegram_result, webapp_result])

        self.assertEqual(merged["schema_version"], worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION)
        self.assertEqual(merged["summary"]["total"], 10)
        self.assertEqual(merged["summary"]["surfaces"]["telegram"]["total"], 6)
        self.assertEqual(merged["summary"]["surfaces"]["webapp"]["total"], 4)
        self.assertIn("role_start_skew", merged)
        first_attempt = merged["attempts"][0]
        for key in {
            "monotonic_timestamp",
            "source_role",
            "source_surface",
            "user_id",
            "offer_public_id",
            "idempotency_key",
            "outcome",
            "latency_ms",
        }:
            self.assertIn(key, first_attempt)

    def test_role_result_merge_rejects_missing_required_attempt_fields(self):
        users = [worker.LoadUserRef(user_id=1, telegram_id=9001), worker.LoadUserRef(user_id=2, telegram_id=9002)]
        plans = worker.build_dual_role_worker_plans(
            run_id="run-4",
            prefix="probe-",
            users=users,
            owner_user_id=1,
            offer_id=42,
            offer_public_id="offer-public-42",
            total_requests=2,
            telegram_ratio=0.5,
            target_rps=10.0,
            amount=1,
            barrier_epoch=1234.5,
        )
        result = worker.build_dry_role_result_artifact(plans["telegram_foreign"], started_epoch=2000.0)
        del result["attempts"][0]["idempotency_key"]

        with self.assertRaises(worker.TradingProbeError):
            worker.merge_role_result_artifacts([result])

    def test_hot_offer_scenario_specs_cover_step_11b3_matrix(self):
        scenarios = worker.build_hot_offer_scenario_specs(
            total_requests=1000,
            telegram_ratio=0.6,
            target_rps=600.0,
            price=100000,
            offer_type="sell",
        )

        names = {scenario.name for scenario in scenarios}
        self.assertEqual(
            names,
            {
                "webapp_full_fill",
                "bot_full_fill",
                "webapp_partial_fill",
                "bot_partial_fill",
                "webapp_retail_lot",
                "bot_retail_lot",
            },
        )
        self.assertEqual({scenario.origin for scenario in scenarios}, {"webapp", "bot"})
        self.assertTrue(any(scenario.expected_winner_count == 1 for scenario in scenarios))
        self.assertTrue(any(scenario.expected_winner_count > 1 for scenario in scenarios))
        self.assertTrue(any(not scenario.is_wholesale and scenario.lot_sizes for scenario in scenarios))
        for scenario in scenarios:
            self.assertEqual(scenario.expected_completed_quantity, scenario.quantity)
            self.assertGreaterEqual(scenario.start_burst_request_count, 36)

    def test_hot_offer_scenario_specs_reject_weak_contention(self):
        with self.assertRaises(worker.TradingProbeError):
            worker.build_hot_offer_scenario_specs(
                total_requests=20,
                telegram_ratio=0.6,
                target_rps=600.0,
                price=100000,
                offer_type="sell",
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.build_hot_offer_scenario_specs(
                total_requests=1000,
                telegram_ratio=0.6,
                target_rps=100.0,
                price=100000,
                offer_type="sell",
            )

    def test_hot_offer_acceptance_fails_closed_on_data_corruption(self):
        worker.assert_hot_offer_contention_acceptance(
            persisted_trade_count=1,
            response_success_count=1,
            error_count=0,
            remaining_quantity=0,
            status="completed",
            expected_winner_count=1,
        )

        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=2,
                response_success_count=2,
                error_count=0,
                remaining_quantity=-1,
                status="completed",
                expected_winner_count=1,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=1,
                response_success_count=1,
                error_count=1,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=1,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=1,
                response_success_count=1,
                error_count=0,
                remaining_quantity=0,
                status="active",
                expected_winner_count=1,
            )

    def test_hot_offer_acceptance_validates_quantities_and_ledger(self):
        worker.assert_hot_offer_contention_acceptance(
            persisted_trade_count=4,
            response_success_count=4,
            error_count=0,
            remaining_quantity=0,
            status="completed",
            expected_winner_count=4,
            original_quantity=20,
            completed_trade_quantity=20,
            completed_ledger_count=4,
        )

        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=4,
                response_success_count=4,
                error_count=0,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=4,
                original_quantity=20,
                completed_trade_quantity=25,
                completed_ledger_count=4,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=4,
                response_success_count=4,
                error_count=0,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=4,
                original_quantity=20,
                completed_trade_quantity=20,
                completed_ledger_count=3,
            )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_hot_offer_contention_acceptance(
                persisted_trade_count=4,
                response_success_count=4,
                error_count=0,
                remaining_quantity=0,
                status="completed",
                expected_winner_count=4,
                original_quantity=20,
                completed_trade_quantity=20,
                completed_ledger_count=4,
                failed_internal_ledger_count=1,
            )

    def test_duplicate_replay_acceptance_allows_successful_replay_without_duplicate_trade(self):
        snapshot = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=5,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        worker.assert_duplicate_replay_acceptance(statuses=["success", "success"], persistence=snapshot)
        worker.assert_duplicate_replay_acceptance(statuses=["success", "rejected"], persistence=snapshot)

        corrupted = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=2,
            completed_trade_quantity=10,
            completed_ledger_count=2,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )
        with self.assertRaises(worker.TradingProbeError):
            worker.assert_duplicate_replay_acceptance(statuses=["success", "success"], persistence=corrupted)

    def test_cleanup_prefix_guard_rejects_empty_broad_and_wildcard_prefixes(self):
        self.assertEqual(worker.validate_cleanup_prefix("P7_TRADING_1405_"), "P7_TRADING_1405_")

        for value in ("", "abc", "test", "prod", "stage", "bad%prefix", "bad*prefix", "bad?prefix"):
            with self.subTest(value=value):
                with self.assertRaises(worker.TradingProbeError):
                    worker.validate_cleanup_prefix(value)

    def test_cleanup_prefix_patterns_escape_sql_like_wildcards(self):
        prefix_pattern, contains_pattern = worker.cleanup_prefix_patterns("P7_TRADING_1405_")

        self.assertEqual(prefix_pattern, r"P7\_TRADING\_1405\_%")
        self.assertEqual(contains_pattern, r"%P7\_TRADING\_1405\_%")

    def test_cleanup_dry_run_report_lists_request_and_publication_scope(self):
        plan = worker.CleanupPlan(
            prefix="P7_TRADING_1405_",
            user_ids=[1, 2],
            offer_ids=[10],
            offer_public_ids=["ofr_10"],
            trade_ids=[20],
            offer_request_ids=[30, 31],
            publication_state_ids=[40, 41],
            notification_ids=[50],
            chat_member_ids=[60],
        )

        report = worker.cleanup_report_payload(plan=plan, dry_run=True, deleted_redis_keys=3)

        self.assertTrue(report["dry_run"])
        self.assertEqual(report["planned_counts"]["offer_requests"], 2)
        self.assertEqual(report["planned_counts"]["offer_publication_states"], 2)
        self.assertEqual(report["planned_ids"]["offer_public_ids"], ["ofr_10"])
        self.assertEqual(report["deleted_offer_requests"], 0)
        self.assertEqual(report["deleted_publication_states"], 0)
        self.assertEqual(report["deleted_redis_keys"], 3)

    def test_load_runner_runtime_surface_guard_accepts_expected_roles(self):
        with patch.object(worker.settings, "environment", "staging"), patch.object(
            worker.settings, "trading_bot_service", "load_runner"
        ), patch.object(worker.settings, "server_mode", "foreign"), patch.object(worker.settings, "bot_token", ""):
            payload = worker.assert_load_runner_runtime_surface("telegram_foreign")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["surface"], "telegram")
        self.assertEqual(payload["server_mode"], "foreign")

        with patch.object(worker.settings, "environment", "staging"), patch.object(
            worker.settings, "trading_bot_service", "load_runner"
        ), patch.object(worker.settings, "server_mode", "iran"), patch.object(worker.settings, "bot_token", None):
            payload = worker.assert_load_runner_runtime_surface("webapp_iran")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["surface"], "webapp")
        self.assertEqual(payload["server_mode"], "iran")

    def test_load_runner_runtime_surface_guard_fails_closed(self):
        with patch.object(worker.settings, "environment", "production"), patch.object(
            worker.settings, "trading_bot_service", "app"
        ), patch.object(worker.settings, "server_mode", "iran"), patch.object(worker.settings, "bot_token", "token"):
            with self.assertRaises(worker.TradingProbeError) as exc_info:
                worker.assert_load_runner_runtime_surface("telegram_foreign")

        message = str(exc_info.exception)
        self.assertIn("ENVIRONMENT must be staging", message)
        self.assertIn("TRADING_BOT_SERVICE must be load_runner", message)
        self.assertIn("SERVER_MODE must be foreign", message)
        self.assertIn("BOT_TOKEN must be empty", message)

    def test_dual_role_final_report_accepts_consistent_hot_offer(self):
        prepare = {
            "run_id": "run-5",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "webapp_hot_offer",
                "expected_winner_count": 1,
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 10,
                "success": 1,
                "rejected": 9,
                "error": 0,
                "business_request_rps": 20.0,
                "telegram_update_rps": 24.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=5,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
        )

        self.assertEqual(report["schema_version"], worker.DUAL_ROLE_FINAL_SCHEMA_VERSION)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["correctness_failures"], [])
        self.assertEqual(report["reports"]["webapp_hot_offer"]["persisted_trade_count"], 1)

    def test_dual_role_final_report_fails_closed_on_request_errors(self):
        prepare = {
            "run_id": "run-6",
            "prefix": "probe-",
            "topology": "single-db staging role-worker smoke",
            "telegram_gateway_boundary": "mock",
            "scenario": {
                "name": "bot_hot_offer",
                "expected_winner_count": 1,
            },
            "offer": {
                "id": 42,
                "owner_user_id": 7,
            },
        }
        merged_result = {
            "schema_version": worker.DUAL_ROLE_MERGED_RESULT_SCHEMA_VERSION,
            "summary": {
                "total": 10,
                "success": 1,
                "rejected": 8,
                "error": 1,
                "business_request_rps": 20.0,
                "telegram_update_rps": 24.0,
                "latency": {},
                "surfaces": {},
            },
            "roles": {},
            "role_start_skew": {},
            "attempts": [
                {
                    "outcome": "error",
                    "detail": "Pool timeout",
                }
            ],
        }
        persistence = worker.HotOfferPersistenceSnapshot(
            offer_id=42,
            original_quantity=5,
            remaining_quantity=0,
            offer_status="completed",
            persisted_trade_count=1,
            completed_trade_quantity=5,
            completed_ledger_count=1,
            trades_without_completed_ledger_count=0,
            failed_internal_ledger_count=0,
            duplicate_replay_ledger_count=0,
        )

        report = worker.build_dual_role_final_report(
            prepare=prepare,
            merged_result=merged_result,
            persistence=persistence,
        )

        self.assertEqual(report["status"], "failed")
        self.assertIn("expected zero internal errors", report["correctness_failures"][0])
        self.assertEqual(report["reports"]["bot_hot_offer"]["attempt_error_details"], {"Pool timeout": 1})

    def test_recording_bot_handles_bound_callback_answer_without_telegram_network(self):
        async def run_probe():
            recorder = worker.RecordingTelegramBot()
            try:
                result = await recorder.bot(
                    AnswerCallbackQuery(
                        callback_query_id="callback-1",
                        text="ok",
                        show_alert=False,
                    )
                )
            finally:
                await recorder.close()
            return result, recorder.callback_answers

        result, callback_answers = asyncio.run(run_probe())

        self.assertTrue(result)
        self.assertEqual(callback_answers["callback-1"]["text"], "ok")


if __name__ == "__main__":
    unittest.main()
