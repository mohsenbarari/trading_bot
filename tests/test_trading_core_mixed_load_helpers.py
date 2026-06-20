import unittest

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


if __name__ == "__main__":
    unittest.main()
