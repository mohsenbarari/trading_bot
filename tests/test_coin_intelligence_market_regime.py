from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_regime import (
    detect_canonical_market_regime,
    operational_market_regime,
    product_market_regime,
    stabilize_market_regime,
)
from core.market_intelligence.market_store import (
    connect_market_store,
    initialize_market_store,
    upsert_observation,
)


class CanonicalMarketRegimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_market_store(Path(self.tempdir.name) / "market.sqlite3")
        initialize_market_store(self.connection)
        self.end = datetime(2026, 8, 19, 10, 10, tzinfo=timezone.utc)
        self.sequence = 0

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def add(
        self,
        *,
        instrument: str,
        source_code: str,
        source_family: str,
        settlement: str,
        form: str,
        event_type: str,
        price: float,
        at: datetime,
        side: str = "MID",
    ) -> None:
        self.sequence += 1
        unit = (
            "TOMAN_PER_MESGHAL_750"
            if instrument.startswith("MELTED_GOLD")
            else "TOMAN_PER_USD"
            if instrument == "USD_HERAT"
            else "USD_PER_TROY_OUNCE"
            if instrument == "XAUUSD"
            else "PROJECT_THOUSAND_TOMAN"
        )
        stamp = at.isoformat().replace("+00:00", "Z")
        event_key = derive_event_key("market-regime-test", self.sequence)
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=event_key,
                source_code=source_code,
                source_family=source_family,
                event_time_utc=stamp,
                available_at_utc=stamp,
                instrument=instrument,
                market_label="TEST_REGIME",
                settlement_term=settlement,
                trade_form=form,
                event_type=event_type,
                side=side,
                price=Decimal(str(price)),
                price_unit=unit,
                quantity=None,
                quantity_unit=None,
                currency="USD" if instrument == "XAUUSD" else "TOMAN",
            ),
        )
        self.connection.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (stamp, event_key),
        )

    def series(
        self,
        *,
        instrument: str,
        prices: list[float],
        settlement: str,
        source_code: str,
        source_family: str,
        form: str,
        event_type: str = "QUOTE",
        side: str = "MID",
    ) -> None:
        for offset, price in enumerate(prices):
            self.add(
                instrument=instrument,
                source_code=source_code,
                source_family=source_family,
                settlement=settlement,
                form=form,
                event_type=event_type,
                price=price,
                at=self.end - timedelta(minutes=len(prices) - 1 - offset),
                side=side,
            )
        self.connection.commit()

    def private(self, prices: list[float], *, settlement: str = "TOMORROW") -> None:
        self.series(
            instrument="MELTED_GOLD_PRIVATE",
            prices=prices,
            settlement=settlement,
            source_code="PRIVATE_GOLD_PAPER_MINUTE",
            source_family="TELEGRAM_PRIVATE",
            form="PAPER_NORMAL",
        )

    def herat(self, prices: list[float], *, settlement: str = "TOMORROW") -> None:
        self.series(
            instrument="USD_HERAT",
            prices=prices,
            settlement=settlement,
            source_code="USD_HERAT",
            source_family="TELEGRAM_PUBLIC",
            form="PAPER_NORMAL",
            event_type="OFFER",
            side="BUY",
        )

    def xau(self, prices: list[float]) -> None:
        self.series(
            instrument="XAUUSD",
            prices=prices,
            settlement="SPOT",
            source_code="XAUUSD",
            source_family="EXTERNAL_MARKET",
            form="NOT_APPLICABLE",
        )

    def coin(self, instrument: str, prices: list[float], *, settlement: str = "TOMORROW") -> None:
        self.series(
            instrument=instrument,
            prices=prices,
            settlement=settlement,
            source_code="GROUP_1",
            source_family="GROUP",
            form="PHYSICAL",
            event_type="OFFER",
            side="SELL",
        )

    def classify(self, settlement: str = "TOMORROW") -> dict:
        return detect_canonical_market_regime(self.connection, self.end, settlement)

    def test_private_melted_is_the_largest_weight_and_aligned_sources_classify_up(self) -> None:
        self.private([80_000_000 + index * 25_000 for index in range(7)])
        self.herat([100_000 + index * 30 for index in range(7)])
        self.xau([4_500 + index * 0.8 for index in range(7)])
        self.coin("COIN_IMAM", [190_000 + index * 100 for index in range(7)])

        result = self.classify()

        self.assertEqual((result["status"], result["regime"], result["direction_state"]), ("OBSERVED", "UP", "UP"))
        weights = result["component_weights_normalized"]
        self.assertGreater(weights["PRIVATE_MELTED_GOLD"], weights["USD_HERAT"])
        self.assertGreater(weights["USD_HERAT"], weights["XAUUSD"])
        self.assertTrue(result["private_melted_anchor_present"])

    def test_highly_oscillatory_private_market_is_not_mislabelled_directional(self) -> None:
        self.private([80_000_000, 81_000_000, 79_000_000, 81_000_000, 79_000_000, 81_000_000, 80_000_000])

        result = self.classify()

        self.assertEqual(result["regime"], "SHOCK")
        self.assertEqual(result["volatility_state"], "SHOCK")
        self.assertNotIn(result["direction_state"], {"UP", "DOWN"})

    def test_two_points_do_not_create_a_directional_regime(self) -> None:
        self.private([80_000_000, 81_000_000])

        result = self.classify()

        self.assertEqual((result["status"], result["regime"], result["confidence"]), ("NO_DATA", "UNKNOWN", 0.0))

    def test_missing_private_anchor_caps_confidence_and_marks_degraded(self) -> None:
        self.herat([100_000 + index * 30 for index in range(7)])
        self.xau([4_500 + index * 0.8 for index in range(7)])
        self.coin("COIN_IMAM", [190_000 + index * 100 for index in range(7)])

        result = self.classify()

        self.assertEqual((result["status"], result["quality"]), ("OBSERVED", "DEGRADED"))
        self.assertFalse(result["private_melted_anchor_present"])
        self.assertLessEqual(result["confidence"], 0.65)

    def test_live_coin_books_confirm_without_one_instrument_dominating(self) -> None:
        self.private([80_000_000] * 7)
        self.herat([100_000 + index * 20 for index in range(7)])
        self.coin("COIN_IMAM", [190_000 + index * 120 for index in range(7)])
        self.coin("COIN_BAHAR", [175_000 + index * 100 for index in range(7)])

        result = self.classify()
        coin = next(row for row in result["components"] if row["name"] == "LIVE_COIN_MARKET")

        self.assertEqual(coin["instrument_count"], 2)
        self.assertEqual(set(coin["instruments"]), {"COIN_IMAM", "COIN_BAHAR"})
        self.assertGreater(coin["direction_strength"], 0)

    def test_cash_does_not_consume_tomorrow_coin_book_as_cash(self) -> None:
        self.private([80_000_000] * 7, settlement="TODAY")
        self.coin("COIN_IMAM", [190_000 + index * 100 for index in range(7)], settlement="TOMORROW")

        result = self.classify("CASH")

        self.assertNotIn("LIVE_COIN_MARKET", {row["name"] for row in result["components"]})

    def test_product_projection_abstains_on_no_data_and_maps_shock_to_volatile(self) -> None:
        empty = product_market_regime(self.classify())
        self.assertEqual(empty["status"], "ABSTAIN")
        self.private([80_000_000, 81_000_000, 79_000_000, 81_000_000, 79_000_000, 81_000_000, 80_000_000])
        volatile = product_market_regime(self.classify())
        self.assertEqual((volatile["status"], volatile["label"]), ("OBSERVED", "VOLATILE"))

    def test_product_projection_abstains_below_confidence_gate(self) -> None:
        self.xau([4_500 + index * 0.8 for index in range(7)])

        projected = product_market_regime(self.classify())

        self.assertEqual(projected["status"], "ABSTAIN")
        self.assertEqual(
            projected["reason"],
            "MARKET_REGIME_CONFIDENCE_BELOW_PRODUCT_GATE",
        )

    def test_operational_gate_retains_diagnostics_but_removes_low_confidence_bias(self) -> None:
        self.xau([4_500 + index * 0.8 for index in range(7)])

        classified = self.classify()
        gated = operational_market_regime(classified)

        self.assertEqual(gated["status"], "ABSTAIN")
        self.assertEqual(gated["regime"], "UNKNOWN")
        self.assertIsNone(gated["direction_score"])
        self.assertEqual(gated["confidence"], 0.0)
        self.assertEqual(gated["classifier_regime"], classified["regime"])
        self.assertGreater(gated["classifier_confidence"], 0.0)

    def test_stabilizer_rejects_one_refresh_flicker_and_accepts_confirmation(self) -> None:
        base = {
            "status": "OBSERVED",
            "quality": "FULL",
            "regime": "RANGE",
            "direction_state": "RANGE",
            "volatility_state": "NORMAL",
            "phase": "STABLE",
            "confidence": 0.8,
            "method": "private-melted-led-market-regime-v2",
        }
        candidate = dict(base, regime="UP", direction_state="UP", confidence=0.7)

        held = stabilize_market_regime(candidate, base)
        accepted = stabilize_market_regime(candidate, held)

        self.assertEqual((held["regime"], held["candidate_regime"], held["candidate_streak"]), ("RANGE", "UP", 1))
        self.assertEqual((accepted["regime"], accepted["candidate_regime"], accepted["candidate_streak"]), ("UP", None, 0))

    def test_stabilizer_does_not_hide_total_data_loss(self) -> None:
        previous = {
            "status": "OBSERVED",
            "regime": "UP",
            "direction_state": "UP",
            "volatility_state": "NORMAL",
            "confidence": 0.8,
            "method": "private-melted-led-market-regime-v2",
        }
        missing = {
            "status": "NO_DATA",
            "regime": "UNKNOWN",
            "direction_state": "UNKNOWN",
            "volatility_state": "UNKNOWN",
            "confidence": 0.0,
            "method": "private-melted-led-market-regime-v2",
        }

        result = stabilize_market_regime(missing, previous)

        self.assertEqual((result["status"], result["regime"]), ("NO_DATA", "UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
