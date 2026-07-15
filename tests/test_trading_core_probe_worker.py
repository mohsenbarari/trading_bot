import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

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
