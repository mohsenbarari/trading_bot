import unittest

from scripts.trading_core_probe_worker import (
    TradingProbeError,
    assert_race_acceptance,
    summarize_samples,
)


class TradingCoreProbeWorkerTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
