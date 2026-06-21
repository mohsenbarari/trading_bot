import importlib.util
import asyncio
import sys
import unittest
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
