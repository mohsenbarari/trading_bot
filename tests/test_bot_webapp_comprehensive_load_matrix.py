import importlib.util
import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "scripts" / "run_bot_webapp_comprehensive_load_matrix.py"

spec = importlib.util.spec_from_file_location("run_bot_webapp_comprehensive_load_matrix", MODULE_PATH)
matrix_runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = matrix_runner
spec.loader.exec_module(matrix_runner)


class BotWebAppComprehensiveLoadMatrixTests(unittest.TestCase):
    def test_matrix_covers_required_scenario_families(self):
        scenarios = matrix_runner.build_comprehensive_scenarios()
        family_counts = {}
        for scenario in scenarios:
            family_counts[scenario.family] = family_counts.get(scenario.family, 0) + 1

        self.assertEqual(len(scenarios), 228)
        self.assertEqual(
            family_counts,
            {
                "active_view": 24,
                "after_completed_reject": 12,
                "after_manual_expiry_reject": 12,
                "after_time_expiry_reject": 12,
                "create_offer": 12,
                "manual_expire_contention": 12,
                "manual_expire_non_concurrent": 24,
                "market_history_view": 36,
                "public_detail_view": 48,
                "time_expiry": 12,
                "trade_concurrent": 12,
                "trade_non_concurrent": 12,
            },
        )

    def test_matrix_preserves_offer_origin_type_shape_and_surface_coverage(self):
        scenarios = matrix_runner.build_comprehensive_scenarios()

        self.assertEqual({scenario.offer_type for scenario in scenarios}, {"buy", "sell"})
        self.assertEqual({scenario.shape for scenario in scenarios}, set(matrix_runner.SHAPES))
        self.assertEqual(set(matrix_runner.SHAPES), {"wholesale_full", "retail_two_lot", "retail_three_lot"})
        for shape in matrix_runner.SHAPES.values():
            self.assertEqual(shape.request_amount * shape.expected_winner_count, shape.quantity)
            if shape.expected_winner_count > 1:
                self.assertFalse(shape.is_wholesale)
                self.assertEqual(sum(shape.lot_sizes), shape.quantity)
        self.assertEqual(
            {
                scenario.offer_origin
                for scenario in scenarios
                if scenario.offer_origin is not None
            },
            {"bot", "webapp"},
        )
        self.assertEqual(
            {
                scenario.request_surface
                for scenario in scenarios
                if scenario.request_surface is not None
            },
            {"telegram", "webapp"},
        )

    def test_surface_distribution_keeps_sixty_forty_mix(self):
        surfaces = [matrix_runner.surface_for_index(index, 0.6) for index in range(1000)]

        self.assertEqual(surfaces.count("telegram"), 600)
        self.assertEqual(surfaces.count("webapp"), 400)

    def test_reset_scenario_user_runtime_state_dedupes_users(self):
        users = [
            matrix_runner.worker.LoadUserRef(user_id=9, telegram_id=9009),
            matrix_runner.worker.LoadUserRef(user_id=7, telegram_id=9007),
            matrix_runner.worker.LoadUserRef(user_id=9, telegram_id=9010),
        ]
        cleanup = AsyncMock(return_value=3)

        async def run_probe():
            with patch.object(matrix_runner.worker, "cleanup_redis_for_user_ids", cleanup):
                return await matrix_runner.reset_scenario_user_runtime_state(users)

        deleted = asyncio.run(run_probe())

        self.assertEqual(deleted, 3)
        cleanup.assert_awaited_once_with([7, 9])

    def test_filter_scenarios_by_family_and_id(self):
        scenarios = matrix_runner.build_comprehensive_scenarios()

        trade_only = matrix_runner.filter_scenarios(
            scenarios,
            families={"trade_concurrent"},
            names=set(),
            max_scenarios=None,
        )
        self.assertEqual(len(trade_only), 12)
        selected = matrix_runner.filter_scenarios(
            scenarios,
            families=set(),
            names={scenarios[0].scenario_id},
            max_scenarios=None,
        )
        self.assertEqual(selected, [scenarios[0]])

    def test_summarize_outcomes_reports_attempt_start_rate_separately(self):
        outcomes = [
            matrix_runner.AttemptOutcome(status="success", latency_ms=1000, start_offset_seconds=0.0),
            matrix_runner.AttemptOutcome(status="rejected", latency_ms=1000, start_offset_seconds=0.01),
            matrix_runner.AttemptOutcome(status="rejected", latency_ms=1000, start_offset_seconds=0.02),
        ]

        summary = matrix_runner.summarize_outcomes(outcomes, elapsed_seconds=1.02)

        self.assertLess(summary["business_request_rps"], 3.0)
        self.assertEqual(summary["attempt_start_elapsed_seconds"], 0.02)
        self.assertEqual(summary["attempt_start_rps"], 150.0)

    def test_write_admission_limit_only_applies_to_write_heavy_non_contention_families(self):
        scenarios = {
            scenario.family: scenario
            for scenario in matrix_runner.build_comprehensive_scenarios()
        }

        self.assertEqual(
            matrix_runner.write_admission_max_concurrency_for_scenario(scenarios["create_offer"], 24),
            24,
        )
        self.assertEqual(
            matrix_runner.write_admission_max_concurrency_for_scenario(scenarios["trade_non_concurrent"], 24),
            24,
        )
        self.assertEqual(
            matrix_runner.write_admission_max_concurrency_for_scenario(
                scenarios["manual_expire_non_concurrent"],
                24,
            ),
            24,
        )
        self.assertIsNone(
            matrix_runner.write_admission_max_concurrency_for_scenario(scenarios["trade_concurrent"], 24)
        )
        self.assertIsNone(
            matrix_runner.write_admission_max_concurrency_for_scenario(
                scenarios["after_manual_expiry_reject"],
                24,
            )
        )
        self.assertIsNone(
            matrix_runner.write_admission_max_concurrency_for_scenario(scenarios["create_offer"], 0)
        )

    def test_run_scheduled_attempts_honors_max_concurrency(self):
        running = 0
        peak_running = 0

        async def attempt(_index):
            nonlocal running, peak_running
            running += 1
            peak_running = max(peak_running, running)
            await asyncio.sleep(0.01)
            running -= 1
            return "success"

        async def run_probe():
            return await matrix_runner.run_scheduled_attempts(
                total=8,
                target_rps=1000,
                attempt=attempt,
                max_concurrency=2,
            )

        outcomes, _elapsed = asyncio.run(run_probe())
        summary = matrix_runner.summarize_outcomes(outcomes, elapsed_seconds=1.0)

        self.assertEqual(peak_running, 2)
        self.assertEqual(summary["success"], 8)
        self.assertIn("admission_wait", summary)
        self.assertGreater(summary["admission_wait"]["max_ms"], 0)

    def test_fast_seed_bot_offer_uses_foreign_direct_create_with_channel_message_id(self):
        calls = {}
        owner = matrix_runner.worker.LoadUserRef(user_id=7, telegram_id=7007)
        shape = matrix_runner.SHAPES["wholesale_full"]

        async def fake_create_offer_for_user(**kwargs):
            calls["create"] = kwargs
            calls["server"] = matrix_runner.worker.current_server()
            return 42

        async def fail_dispatcher_create(**_kwargs):
            raise AssertionError("fast seed must not use dispatcher offer creation")

        async def run_probe():
            with patch.object(
                matrix_runner.worker,
                "create_offer_for_user",
                new=fake_create_offer_for_user,
            ), patch.object(
                matrix_runner.worker,
                "create_bot_offer_with_dispatcher",
                new=fail_dispatcher_create,
            ):
                return await matrix_runner.create_offer(
                    origin="bot",
                    owner=owner,
                    commodity_id=11,
                    commodity_name="امام",
                    shape=shape,
                    offer_type="buy",
                    prefix="probe-",
                    index=2003,
                    fast_seed_bot_offer=True,
                )

        offer_id = asyncio.run(run_probe())

        self.assertEqual(offer_id, 42)
        self.assertEqual(calls["server"], matrix_runner.SERVER_FOREIGN)
        self.assertEqual(calls["create"]["user_id"], owner.user_id)
        self.assertEqual(calls["create"]["channel_message_id"], 900_002_003)

    def test_telegram_trade_attempt_can_preconfirm_callback_on_foreign_server(self):
        calls = {}
        user = matrix_runner.worker.LoadUserRef(user_id=7, telegram_id=7007)
        offer = SimpleNamespace(id=42, offer_public_id="ofr_42")

        async def fake_load_offer_snapshot(offer_id):
            calls["loaded_offer_id"] = offer_id
            return offer

        async def fake_preconfirm_bot_trade_callback(**kwargs):
            calls["preconfirm"] = kwargs
            calls["preconfirm_server"] = matrix_runner.worker.current_server()
            return False

        async def fake_execute_bot_trade_with_dispatcher(**kwargs):
            calls["bot_trade"] = kwargs
            calls["bot_trade_server"] = matrix_runner.worker.current_server()
            return "success"

        async def run_probe():
            with patch.object(
                matrix_runner.worker,
                "load_offer_snapshot",
                new=fake_load_offer_snapshot,
            ), patch.object(
                matrix_runner.worker,
                "preconfirm_bot_trade_callback",
                new=fake_preconfirm_bot_trade_callback,
            ), patch.object(
                matrix_runner.worker,
                "execute_bot_trade_with_dispatcher",
                new=fake_execute_bot_trade_with_dispatcher,
            ):
                return await matrix_runner.execute_trade_attempt(
                    surface="telegram",
                    harness=SimpleNamespace(),
                    user=user,
                    offer_id=42,
                    amount=5,
                    prefix="probe-",
                    index=3,
                    preconfirm_telegram=True,
                )

        status = asyncio.run(run_probe())

        self.assertEqual(status, "success")
        self.assertEqual(calls["loaded_offer_id"], 42)
        self.assertEqual(calls["preconfirm_server"], matrix_runner.SERVER_FOREIGN)
        self.assertEqual(calls["bot_trade_server"], matrix_runner.SERVER_FOREIGN)
        self.assertTrue(calls["bot_trade"]["preconfirmed"])

    def test_telegram_trade_attempt_records_rejection_answer_text(self):
        error_details = []
        user = matrix_runner.worker.LoadUserRef(user_id=7, telegram_id=7007)
        offer = SimpleNamespace(id=42, offer_public_id="ofr_42")

        async def fake_load_offer_snapshot(_offer_id):
            return offer

        async def fake_preconfirm_bot_trade_callback(**_kwargs):
            return False

        async def fake_execute_bot_trade_with_dispatcher(**kwargs):
            kwargs["phase_details"]["second_answer_text"] = "برای تایید دوباره روی همان دکمه بزنید ☑️"
            return "rejected"

        async def run_probe():
            with patch.object(
                matrix_runner.worker,
                "load_offer_snapshot",
                new=fake_load_offer_snapshot,
            ), patch.object(
                matrix_runner.worker,
                "preconfirm_bot_trade_callback",
                new=fake_preconfirm_bot_trade_callback,
            ), patch.object(
                matrix_runner.worker,
                "execute_bot_trade_with_dispatcher",
                new=fake_execute_bot_trade_with_dispatcher,
            ):
                return await matrix_runner.execute_trade_attempt(
                    surface="telegram",
                    harness=SimpleNamespace(),
                    user=user,
                    offer_id=42,
                    amount=5,
                    prefix="probe-",
                    index=3,
                    error_details=error_details,
                    preconfirm_telegram=True,
                    record_rejected_details=True,
                )

        status = asyncio.run(run_probe())

        self.assertEqual(status, "rejected")
        self.assertEqual(
            error_details,
            ["telegram_callback_rejected: برای تایید دوباره روی همان دکمه بزنید ☑️"],
        )


if __name__ == "__main__":
    unittest.main()
