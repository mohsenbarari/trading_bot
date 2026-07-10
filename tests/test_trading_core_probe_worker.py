import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.trading_core_probe_worker import (
    TradingProbeError,
    assert_race_acceptance,
    run_manual_expiry_race_command,
    run_time_expiry_race_command,
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

    def test_standalone_manual_expiry_registers_model_event_listeners(self) -> None:
        args = SimpleNamespace(prepare="/missing/manual-expiry-prepare.json")

        with patch("scripts.trading_core_probe_worker.setup_event_listeners") as setup:
            with self.assertRaises(TradingProbeError):
                asyncio.run(run_manual_expiry_race_command(args))

        setup.assert_called_once_with()

    def test_standalone_time_expiry_registers_model_event_listeners(self) -> None:
        args = SimpleNamespace(prepare="/missing/time-expiry-prepare.json")

        with patch("scripts.trading_core_probe_worker.setup_event_listeners") as setup:
            with self.assertRaises(TradingProbeError):
                asyncio.run(run_time_expiry_race_command(args))

        setup.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
