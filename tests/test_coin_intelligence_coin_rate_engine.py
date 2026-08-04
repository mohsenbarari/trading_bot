"""Offline contract tests for deterministic coin-rate ranges."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_rate_engine import build_coin_rate_estimates
from core.market_intelligence.market_contracts import MarketObservation, derive_event_key
from core.market_intelligence.market_store import connect_market_store, initialize_market_store, upsert_observation


class CoinRateEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_market_store(Path(self.tempdir.name) / "market.sqlite3")
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def add(self, key: str, *, instrument: str, price: int, unit: str, at: str, settlement: str, form: str, event_type: str = "QUOTE") -> None:
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=derive_event_key("rate-engine", key), source_code="TEST_RATE", source_family="MANUAL_REVIEW",
                event_time_utc=at, available_at_utc=at, instrument=instrument, market_label="TEST_RATE",
                settlement_term=settlement, trade_form=form, event_type=event_type, side="MID",
                price=Decimal(price), price_unit=unit, quantity=None, quantity_unit=None,
            ),
        )

    def rate(self, code: str, settlement: str):
        return next(item for item in build_coin_rate_estimates(self.connection, as_of_utc="2026-08-04T10:10:00Z") if item.commodity_code == code and item.settlement_term == settlement)

    def test_low_date_uses_physical_melted_without_any_coin_offer(self) -> None:
        self.add("gold", instrument="MELTED_GOLD_PRIVATE", price=803_000_000, unit="IRT_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TODAY", form="PHYSICAL")
        self.connection.commit()
        bahar = self.rate("BAHAR", "CASH")
        self.assertEqual((bahar.status, bahar.method, bahar.estimated_project_price), ("ESTIMATED", "LOW_DATE_MELTED_INTRINSIC", 180_900))
        self.assertLess(bahar.upper_project_price - bahar.lower_project_price, 5_000)

    def test_same_coin_anchor_moves_with_new_underlying_price(self) -> None:
        self.add("gold-old", instrument="MELTED_GOLD_PRIVATE", price=803_000_000, unit="IRT_PER_MESGHAL_750", at="2026-08-04T10:00:00Z", settlement="TODAY", form="PHYSICAL")
        self.add("gold-now", instrument="MELTED_GOLD_PRIVATE", price=810_000_000, unit="IRT_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TODAY", form="PHYSICAL")
        self.add("imam", instrument="COIN_IMAM", price=186_900, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:01:00Z", settlement="CASH", form="PHYSICAL", event_type="TRADE")
        self.connection.commit()
        imam = self.rate("IMAM", "CASH")
        self.assertEqual((imam.status, imam.method, imam.estimated_project_price), ("ESTIMATED", "SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER", 188_500))
        self.assertLess(imam.upper_project_price - imam.lower_project_price, 6_000)

    def test_paper_fallback_is_visible_and_no_high_coin_anchor_abstains(self) -> None:
        self.add("paper", instrument="MELTED_GOLD_PRIVATE", price=805_000_000, unit="IRT_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.connection.commit()
        low = self.rate("QUARTER_LOW_DATE", "TOMORROW")
        high = self.rate("IMAM", "TOMORROW")
        self.assertEqual((low.status, low.confidence), ("ESTIMATED", "LOW_PAPER_FALLBACK"))
        self.assertEqual((high.status, high.reason), ("NO_DATA", "NO_SAFE_SAME_COMMODITY_ANCHOR"))

    def test_paper_up_regime_only_widens_positive_side_with_a_bounded_interval(self) -> None:
        for index, price in enumerate((800_000_000, 800_200_000, 801_000_000, 804_000_000), start=6):
            self.add(f"paper-{index}", instrument="MELTED_GOLD_PRIVATE", price=price, unit="IRT_PER_MESGHAL_750", at=f"2026-08-04T10:{index:02d}:00Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.connection.commit()
        low = self.rate("BAHAR", "TOMORROW")
        self.assertEqual(low.market_regime, "UP")
        self.assertGreaterEqual(low.upper_project_price - low.estimated_project_price, low.estimated_project_price - low.lower_project_price)
        self.assertLess(low.upper_project_price - low.lower_project_price, 5_000)


if __name__ == "__main__":
    unittest.main()
