import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from scripts import trading_core_probe_worker as worker
from scripts.trading_core_probe_worker import (
    TradingProbeError,
    assert_race_barrier_lateness,
    assert_race_acceptance,
    build_bot_offer_text,
    run_manual_expiry_race_command,
    set_prepare_barrier_command,
    run_time_expiry_race_command,
    summarize_samples,
)


class TradingCoreProbeWorkerTests(unittest.TestCase):
    def test_dispatch_denies_legacy_dependency_commands_even_when_environment_is_spoofed(self) -> None:
        args = SimpleNamespace(command="cleanup", dry_run=False)
        cleanup = AsyncMock(side_effect=AssertionError("retired command reached cleanup"))

        with patch.dict(
            worker.os.environ,
            {
                "ENVIRONMENT": "development",
                worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE,
                worker.PRODUCTION_CLEANUP_CONFIRM_ENV: worker.PRODUCTION_CLEANUP_CONFIRM_VALUE,
            },
            clear=False,
        ), patch.object(worker.settings, "environment", "development"), patch.object(
            worker,
            "cleanup_command",
            new=cleanup,
        ):
            with self.assertRaisesRegex(worker.TradingProbeError, "retired and hard-disabled"):
                asyncio.run(worker.dispatch(args))

        cleanup.assert_not_awaited()

    def test_cli_denies_legacy_cleanup_before_handler_when_environment_is_spoofed(self) -> None:
        cleanup = AsyncMock(side_effect=AssertionError("retired command reached cleanup"))

        with patch.dict(
            worker.os.environ,
            {
                "ENVIRONMENT": "development",
                worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE,
                worker.PRODUCTION_CLEANUP_CONFIRM_ENV: worker.PRODUCTION_CLEANUP_CONFIRM_VALUE,
            },
            clear=False,
        ), patch.object(worker.settings, "environment", "development"), patch.object(
            worker,
            "cleanup_command",
            new=cleanup,
        ), patch.object(worker, "print_json") as print_json:
            exit_code = worker.main(
                [
                    "cleanup",
                    "--prefix",
                    "PFM_20260728_legacy_fence_",
                    "--allow-production-hard-delete",
                ]
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(print_json.call_args.args[0]["error_type"], "TradingProbeError")
        self.assertIn("retired and hard-disabled", print_json.call_args.args[0]["message"])
        cleanup.assert_not_awaited()

    def test_dispatch_keeps_artifact_only_worker_commands_available(self) -> None:
        args = SimpleNamespace(command="run-dual-role-artifact-smoke")
        command = AsyncMock(return_value=0)

        with patch.object(worker, "run_dual_role_artifact_smoke_command", new=command):
            result = asyncio.run(worker.dispatch(args))

        self.assertEqual(result, 0)
        command.assert_awaited_once_with(args)

    def test_production_confirmation_environment_cannot_enable_load_runner(self) -> None:
        with patch.dict(
            worker.os.environ,
            {worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE},
            clear=False,
        ), patch.object(worker.settings, "environment", "production"), patch.object(
            worker.settings, "trading_bot_service", "load_runner"
        ), patch.object(worker.settings, "server_mode", "foreign"), patch.object(worker.settings, "bot_token", ""):
            with self.assertRaisesRegex(worker.TradingProbeError, "retired"):
                worker.assert_load_runner_runtime_surface(
                    "telegram_foreign",
                    allow_production=True,
                    prefix="PFM_20260728_legacy_fence_",
                )

    def test_direct_mutable_handlers_deny_environment_spoofing_before_dependency_access(self) -> None:
        handlers = {
            "benchmark": worker.run_benchmark,
            "mixed_load": worker.run_mixed_load_benchmark,
            "hot_offer": worker.run_hot_offer_scenarios_command,
            "cleanup": worker.cleanup_command,
            "negative_guard": worker.run_negative_guard_case_command,
            "unsupported_policy": worker.run_unsupported_policy_case_command,
            "seed": worker.seed_dual_role_users_command,
            "verify": worker.verify_dual_role_users_command,
            "prepare": worker.prepare_dual_role_run_command,
            "manual_race": worker.run_manual_expiry_race_command,
            "time_race": worker.run_time_expiry_race_command,
            "read_during_write": worker.run_read_during_write_command,
            "finalize": worker.finalize_dual_role_run_command,
            "observability": worker.observability_snapshot_command,
            "sync": worker.sync_prefix_catchup_command,
            "run_role": worker.run_role_plan_command,
            "visibility": worker.wait_offer_visible_command,
            "rebase": worker.rebase_role_plan_command,
        }

        with patch.dict(
            worker.os.environ,
            {
                "ENVIRONMENT": "development",
                worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE,
                worker.PRODUCTION_CLEANUP_CONFIRM_ENV: worker.PRODUCTION_CLEANUP_CONFIRM_VALUE,
            },
            clear=False,
        ):
            for name, handler in handlers.items():
                with self.subTest(name=name), self.assertRaisesRegex(TradingProbeError, "hard-disabled"):
                    asyncio.run(handler(SimpleNamespace()))

    def test_direct_high_level_dependency_helpers_deny_environment_spoofing(self) -> None:
        helpers = {
            "warm_dependencies": lambda: worker.warm_load_runner_dependencies(db_connections=1),
            "cleanup": lambda: worker.cleanup_prefix("PFM_20260728_legacy_fence_"),
            "sync": lambda: worker.push_prefix_change_logs_to_peer("PFM_20260728_legacy_fence_"),
            "fixture": lambda: worker.create_fixture_users("PFM_20260728_legacy_fence_"),
            "load_fixture": lambda: worker.create_load_fixture_users(
                "PFM_20260728_legacy_fence_", user_count=3
            ),
            "persistence": lambda: worker.inspect_hot_offer_persistence(1),
            "visibility": lambda: worker.wait_for_offer_visibility(
                offer_id=1,
                offer_public_id=None,
                timeout_seconds=0,
                poll_seconds=0,
            ),
            "role_plan": lambda: worker.run_role_worker_plan({}),
            "contention": lambda: worker.run_hot_offer_contention(
                prefix="PFM_20260728_legacy_fence_",
                offer_id=1,
                owner_user_id=1,
                users=[],
                total_requests=1,
                telegram_ratio=0,
                target_rps=1,
                amount=1,
                expected_winner_count=1,
            ),
            "scenario": lambda: worker.run_hot_offer_scenario(
                prefix="PFM_20260728_legacy_fence_",
                scenario=None,
                users=[],
                commodity_id=1,
                commodity_name="gold",
                index=1,
            ),
            "duplicate": lambda: worker.run_duplicate_replay_probe(
                prefix="PFM_20260728_legacy_fence_",
                users=[],
                commodity_id=1,
                commodity_name="gold",
                price=1,
                offer_type="sell",
            ),
            "notifications": lambda: worker.run_notification_fanout(
                user_ids=[], prefix="PFM_20260728_legacy_fence_", iterations=1
            ),
            "race": lambda: worker.run_race_probe(
                prefix="PFM_20260728_legacy_fence_",
                fixture=None,
                commodity_id=1,
                concurrency=1,
            ),
        }

        with patch.dict(
            worker.os.environ,
            {
                "ENVIRONMENT": "development",
                worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE,
                worker.PRODUCTION_CLEANUP_CONFIRM_ENV: worker.PRODUCTION_CLEANUP_CONFIRM_VALUE,
            },
            clear=False,
        ):
            for name, invocation in helpers.items():
                with self.subTest(name=name), self.assertRaisesRegex(TradingProbeError, "hard-disabled"):
                    asyncio.run(invocation())

    def test_direct_low_level_runtime_helpers_deny_environment_spoofing(self) -> None:
        helpers = {
            "redis_cleanup": lambda: worker.cleanup_redis_for_user_ids([1]),
            "fixture_creation": lambda: worker.create_fixture_users("PFM_20260728_legacy_fence_"),
            "offer_creation": lambda: worker.create_offer_for_user(),
            "trade_execution": lambda: worker.execute_trade_for_user(),
            "router_patch": lambda: worker.patched_trading_boundaries(),
            "role_harness": worker.AiogramDispatcherHarness,
        }

        with patch.dict(
            worker.os.environ,
            {
                "ENVIRONMENT": "development",
                worker.PRODUCTION_ROLE_WORKER_CONFIRM_ENV: worker.PRODUCTION_ROLE_WORKER_CONFIRM_VALUE,
                worker.PRODUCTION_CLEANUP_CONFIRM_ENV: worker.PRODUCTION_CLEANUP_CONFIRM_VALUE,
            },
            clear=False,
        ):
            for name, invocation in helpers.items():
                with self.subTest(name=name), self.assertRaisesRegex(TradingProbeError, "hard-disabled"):
                    invocation()

    def test_runtime_helper_registry_covers_all_retained_dependency_categories(self) -> None:
        expected = {
            "cleanup_redis_for_user_ids",
            "collect_cleanup_plan",
            "delete_cleanup_plan",
            "create_fixture_users",
            "create_load_fixture_users",
            "create_offer_for_user",
            "execute_trade_for_user",
            "expire_offer_for_user",
            "push_prefix_change_logs_to_peer",
            "run_offer_expiry_cycle_for_server",
            "execute_bot_trade_with_dispatcher",
            "run_hot_offer_contention",
            "run_race_probe",
            "run_benchmark",
            "sync_prefix_catchup_command",
        }

        self.assertTrue(expected.issubset(set(worker._LEGACY_TWO_SERVER_RUNTIME_HELPER_NAMES)))

    def test_bot_offer_matrix_uses_current_cash_settlement_prefix(self) -> None:
        buy_text, buy_marker = build_bot_offer_text(
            owner_user_id=17,
            commodity_name="امام",
            prefix="matrix_",
            quantity=20,
            price=176000,
            offer_type="buy",
        )
        sell_text, sell_marker = build_bot_offer_text(
            owner_user_id=18,
            commodity_name="ربع",
            prefix="matrix_",
            quantity=40,
            price=178000,
            offer_type="sell",
            is_wholesale=False,
            lot_sizes=[30, 10],
        )

        self.assertEqual(buy_text, "خ ن امام 20 عدد 176000: matrix_ bot hot 17")
        self.assertEqual(buy_marker, "matrix_ bot hot 17")
        self.assertEqual(sell_text, "ف ن ربع 40 عدد 178000 30 10: matrix_ bot hot 18")
        self.assertEqual(sell_marker, "matrix_ bot hot 18")

    def test_summarize_samples_reports_tail_latency(self) -> None:
        summary = summarize_samples([10.0, 20.0, 30.0, 40.0])

        self.assertEqual(summary["count"], 4)
        self.assertEqual(summary["p50_ms"], 20.0)
        self.assertEqual(summary["p95_ms"], 40.0)
        self.assertEqual(summary["p99_ms"], 40.0)
        self.assertEqual(summary["max_ms"], 40.0)

    def test_race_acceptance_requires_exactly_one_completed_trade(self) -> None:
        assert_race_acceptance(
            winner_count=1,
            trade_count=1,
            remaining_quantity=0,
            status="completed",
            error_count=0,
        )

        with self.assertRaises(TradingProbeError):
            assert_race_acceptance(
                winner_count=2,
                trade_count=2,
                remaining_quantity=0,
                status="completed",
            )

    def test_race_acceptance_rejects_timeout_or_unexpected_errors(self) -> None:
        with self.assertRaises(TradingProbeError):
            assert_race_acceptance(
                winner_count=1,
                trade_count=1,
                remaining_quantity=0,
                status="completed",
                error_count=1,
            )

    def test_race_barrier_rejects_late_container_start(self) -> None:
        assert_race_barrier_lateness(label="race", scheduled_epoch=10.0, started_epoch=10.5)
        with self.assertRaisesRegex(TradingProbeError, "missed its execution barrier"):
            assert_race_barrier_lateness(label="race", scheduled_epoch=10.0, started_epoch=11.01)

    def test_standalone_manual_expiry_is_denied_before_model_event_listener_registration(self) -> None:
        args = SimpleNamespace(prepare="/missing/manual-expiry-prepare.json")

        with patch("scripts.trading_core_probe_worker.setup_event_listeners") as setup:
            with self.assertRaisesRegex(TradingProbeError, "hard-disabled"):
                asyncio.run(run_manual_expiry_race_command(args))

        setup.assert_not_called()

    def test_standalone_time_expiry_is_denied_before_model_event_listener_registration(self) -> None:
        args = SimpleNamespace(prepare="/missing/time-expiry-prepare.json")

        with patch("scripts.trading_core_probe_worker.setup_event_listeners") as setup:
            with self.assertRaisesRegex(TradingProbeError, "hard-disabled"):
                asyncio.run(run_time_expiry_race_command(args))

        setup.assert_not_called()

    def test_prepare_barrier_refresh_keeps_time_expiry_relative_to_new_barrier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prepare.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": "bot_webapp_mixed_load_prepare_v1",
                        "barrier_epoch": 10.0,
                        "scenario": {"name": "time_expire_trade_race"},
                    }
                ),
                encoding="utf-8",
            )

            result = asyncio.run(
                set_prepare_barrier_command(
                    SimpleNamespace(prepare=str(path), output=None, barrier_epoch=100.0)
                )
            )
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(payload["barrier_epoch"], 100.0)
        self.assertEqual(payload["time_expiry_epoch"], 100.3)
        self.assertEqual(payload["time_expiry_stale_epoch"], 100.25)


if __name__ == "__main__":
    unittest.main()
