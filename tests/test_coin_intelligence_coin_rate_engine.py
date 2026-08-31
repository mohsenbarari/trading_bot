"""Offline contract tests for deterministic coin-rate ranges."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from core.market_intelligence.coin_rate_engine import _coin_anchor, build_coin_rate_estimates
from core.market_intelligence.market_contracts import (
    MarketObservation,
    MarketStoreContractError,
    derive_event_key,
)
from core.market_intelligence.market_store import connect_market_store, initialize_market_store, upsert_observation


class CoinRateEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.connection = connect_market_store(Path(self.tempdir.name) / "market.sqlite3")
        initialize_market_store(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def add(self, key: str, *, instrument: str, price: int, unit: str, at: str, settlement: str, form: str, event_type: str = "QUOTE", is_conditional: bool = False, source_code: str = "TEST_RATE") -> None:
        event_key = derive_event_key("rate-engine", key)
        upsert_observation(
            self.connection,
            MarketObservation(
                event_key=event_key, source_code=source_code, source_family="MANUAL_REVIEW",
                event_time_utc=at, available_at_utc=at, instrument=instrument, market_label="TEST_RATE",
                settlement_term=settlement, trade_form=form, event_type=event_type, side="MID",
                price=Decimal(price), price_unit=unit, quantity=None, quantity_unit=None,
                is_conditional=is_conditional,
            ),
        )
        self.connection.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (at, event_key),
        )

    def rate(self, code: str, settlement: str):
        return next(item for item in build_coin_rate_estimates(self.connection, as_of_utc="2026-08-04T10:10:00Z") if item.commodity_code == code and item.settlement_term == settlement)

    def rate_at(self, code: str, settlement: str, *, at: str):
        return next(
            item
            for item in build_coin_rate_estimates(self.connection, as_of_utc=at)
            if item.commodity_code == code and item.settlement_term == settlement
        )

    def anchor_at(self, code: str, settlement: str, *, at: str):
        return _coin_anchor(
            self.connection,
            as_of=datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(timezone.utc),
            code=code,
            settlement=settlement,
        )

    def test_rate_excludes_fact_inserted_after_evaluation_time(self) -> None:
        self.add(
            "late-local-insert",
            instrument="MELTED_GOLD_PRIVATE",
            price=80_300_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:09:30Z",
            settlement="TODAY",
            form="PHYSICAL",
        )
        self.connection.execute(
            "UPDATE market_observations SET inserted_at_utc=?",
            ("2026-08-04T10:10:00.000001Z",),
        )
        self.connection.commit()

        rate = self.rate("BAHAR", "CASH")

        self.assertEqual((rate.status, rate.reason), ("NO_DATA", "NO_FRESH_MELTED"))

    def test_low_date_uses_physical_melted_without_any_coin_offer(self) -> None:
        self.add("gold", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TODAY", form="PHYSICAL")
        self.connection.commit()
        bahar = self.rate("BAHAR", "CASH")
        self.assertEqual((bahar.status, bahar.method, bahar.estimated_project_price), ("ESTIMATED", "LOW_DATE_MELTED_INTRINSIC", 180_900))
        self.assertEqual(bahar.underlying_age_seconds, 30.0)
        self.assertLess(bahar.upper_project_price - bahar.lower_project_price, 5_000)

    def test_comparable_physical_condition_is_used_but_outlier_condition_is_not(self) -> None:
        self.add("normal-1", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:00Z", settlement="TODAY", form="PHYSICAL", event_type="OFFER")
        self.add("normal-2", instrument="MELTED_GOLD_PRIVATE", price=80_310_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:05Z", settlement="TODAY", form="PHYSICAL", event_type="OFFER")
        self.add("comparable", instrument="MELTED_GOLD_PRIVATE", price=80_320_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:10Z", settlement="TODAY", form="PHYSICAL", event_type="OFFER", is_conditional=True)
        for index, at in enumerate(("15", "20", "25"), start=1):
            self.add(f"outlier-{index}", instrument="MELTED_GOLD_PRIVATE", price=82_000_000, unit="TOMAN_PER_MESGHAL_750", at=f"2026-08-04T10:09:{at}Z", settlement="TODAY", form="PHYSICAL", event_type="OFFER", is_conditional=True)
        self.connection.commit()

        bahar = self.rate("BAHAR", "CASH")

        self.assertEqual((bahar.status, bahar.underlying_source), ("ESTIMATED", "PRIVATE_PHYSICAL_TODAY"))
        self.assertEqual(bahar.estimated_project_price, 180_950)

    def test_same_coin_anchor_moves_with_new_underlying_price(self) -> None:
        self.add("gold-old", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:00:00Z", settlement="TODAY", form="PHYSICAL")
        self.add("gold-now", instrument="MELTED_GOLD_PRIVATE", price=81_000_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TODAY", form="PHYSICAL")
        self.add("imam", instrument="COIN_IMAM", price=186_900, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:01:00Z", settlement="CASH", form="PHYSICAL", event_type="TRADE")
        self.connection.commit()
        imam = self.rate("IMAM", "CASH")
        self.assertEqual((imam.status, imam.method, imam.estimated_project_price), ("ESTIMATED", "SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER", 188_500))
        self.assertLess(imam.upper_project_price - imam.lower_project_price, 6_000)

    def test_newest_trade_time_wins_over_later_inserted_backfill(self) -> None:
        self.add("gold-old", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:00:00Z", settlement="TODAY", form="PHYSICAL")
        self.add("newer-trade", instrument="COIN_IMAM", price=186_900, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:05:00Z", settlement="CASH", form="PHYSICAL", event_type="TRADE")
        # Backfill is inserted later (larger id) but represents an older event.
        self.add("older-backfill", instrument="COIN_IMAM", price=180_000, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:01:00Z", settlement="CASH", form="PHYSICAL", event_type="TRADE")
        self.add("gold-now", instrument="MELTED_GOLD_PRIVATE", price=81_000_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TODAY", form="PHYSICAL")
        self.connection.commit()

        imam = self.rate("IMAM", "CASH")

        self.assertEqual(imam.method, "SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER")
        self.assertEqual(imam.anchor_age_seconds, 300.0)
        self.assertEqual(imam.estimated_project_price, 188_500)

    def test_stale_trade_does_not_mask_fresh_offer_anchor(self) -> None:
        self.add(
            "stale-one-gram-trade",
            instrument="COIN_ONE_GRAM",
            price=20_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-01T09:59:59Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.add(
            "fresh-one-gram-offer",
            instrument="COIN_ONE_GRAM",
            price=21_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-08T09:59:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="OFFER",
        )
        self.connection.commit()

        anchor = self.anchor_at("ONE_GRAM", "CASH", at="2026-08-08T10:00:00Z")

        self.assertIsNotNone(anchor)
        assert anchor is not None
        self.assertEqual(anchor[0], 21_000)
        self.assertEqual(anchor[1].isoformat(), "2026-08-08T09:59:00+00:00")

    def test_fresh_trade_is_preferred_over_fresh_offer_anchor(self) -> None:
        self.add(
            "fresh-one-gram-offer",
            instrument="COIN_ONE_GRAM",
            price=21_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-08T09:59:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="OFFER",
        )
        self.add(
            "fresh-one-gram-trade",
            instrument="COIN_ONE_GRAM",
            price=20_500,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-08T09:58:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.connection.commit()

        anchor = self.anchor_at("ONE_GRAM", "CASH", at="2026-08-08T10:00:00Z")

        self.assertIsNotNone(anchor)
        assert anchor is not None
        self.assertEqual(anchor[0], 20_500)
        self.assertEqual(anchor[1].isoformat(), "2026-08-08T09:58:00+00:00")

    def test_trade_without_point_in_time_underlying_does_not_mask_offer(self) -> None:
        self.add(
            "trade-without-underlying",
            instrument="COIN_IMAM",
            price=186_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-04T10:01:00Z",
            settlement="TOMORROW",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.add(
            "offer-with-underlying",
            instrument="COIN_IMAM",
            price=187_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-04T10:09:00Z",
            settlement="TOMORROW",
            form="PHYSICAL",
            event_type="OFFER",
        )
        self.add(
            "offer-underlying",
            instrument="MELTED_GOLD_PRIVATE",
            price=80_300_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:08:30Z",
            settlement="TOMORROW",
            form="PAPER_NORMAL",
        )
        self.add(
            "current-underlying",
            instrument="MELTED_GOLD_PRIVATE",
            price=81_000_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:09:30Z",
            settlement="TOMORROW",
            form="PAPER_NORMAL",
        )
        self.connection.commit()

        imam = self.rate("IMAM", "TOMORROW")

        self.assertEqual(imam.status, "ESTIMATED")
        self.assertTrue(
            imam.method.startswith("SAME_SETTLEMENT_COIN_ANCHOR_TRANSFER")
        )
        self.assertEqual(imam.anchor_age_seconds, 60.0)

    def test_all_stale_coin_anchors_abstain(self) -> None:
        self.add(
            "stale-one-gram-offer",
            instrument="COIN_ONE_GRAM",
            price=21_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-01T09:59:59Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="OFFER",
        )
        self.add(
            "older-one-gram-trade",
            instrument="COIN_ONE_GRAM",
            price=20_500,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-01T09:00:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.connection.commit()

        anchor = self.anchor_at("ONE_GRAM", "CASH", at="2026-08-08T10:00:00Z")

        self.assertIsNone(anchor)

    def test_coin_anchor_at_exact_seven_day_boundary_is_fresh(self) -> None:
        self.add(
            "boundary-one-gram-trade",
            instrument="COIN_ONE_GRAM",
            price=20_500,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-01T10:00:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.connection.commit()

        anchor = self.anchor_at("ONE_GRAM", "CASH", at="2026-08-08T10:00:00Z")

        self.assertIsNotNone(anchor)
        assert anchor is not None
        self.assertEqual(anchor[0], 20_500)
        self.assertEqual(anchor[1].isoformat(), "2026-08-01T10:00:00+00:00")

    def test_future_trade_is_ignored_instead_of_becoming_age_zero(self) -> None:
        self.add(
            "fresh-one-gram-offer",
            instrument="COIN_ONE_GRAM",
            price=21_000,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-08T09:59:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="OFFER",
        )
        self.add(
            "future-one-gram-trade",
            instrument="COIN_ONE_GRAM",
            price=20_500,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-08T10:00:01Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.connection.execute(
            "UPDATE market_observations SET inserted_at_utc=? WHERE event_key=?",
            (
                "2026-08-08T09:59:30Z",
                derive_event_key("rate-engine", "future-one-gram-trade"),
            ),
        )
        self.connection.commit()

        anchor = self.anchor_at("ONE_GRAM", "CASH", at="2026-08-08T10:00:00Z")

        self.assertIsNotNone(anchor)
        assert anchor is not None
        self.assertEqual(anchor[0], 21_000)

    def test_invalid_anchor_timestamp_remains_fail_closed(self) -> None:
        self.add(
            "invalid-one-gram-trade",
            instrument="COIN_ONE_GRAM",
            price=20_500,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-08T09:59:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.connection.execute(
            "UPDATE market_observations SET event_time_utc=? WHERE event_key=?",
            (
                "0000",
                derive_event_key("rate-engine", "invalid-one-gram-trade"),
            ),
        )
        self.connection.commit()

        with self.assertRaisesRegex(
            MarketStoreContractError,
            "coin_anchor_event_time_utc_invalid",
        ):
            self.anchor_at("ONE_GRAM", "CASH", at="2026-08-08T10:00:00Z")

    def test_paper_fallback_is_visible_and_no_high_coin_anchor_abstains(self) -> None:
        self.add("paper", instrument="MELTED_GOLD_PRIVATE", price=80_500_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.connection.commit()
        low = self.rate("QUARTER_LOW_DATE", "TOMORROW")
        high = self.rate("IMAM", "TOMORROW")
        self.assertEqual((low.status, low.confidence), ("ESTIMATED", "LOW_PAPER_FALLBACK"))
        self.assertEqual((high.status, high.reason), ("NO_DATA", "NO_SAFE_SAME_COMMODITY_ANCHOR"))

    def test_fresh_tomorrow_paper_bridges_quiet_cash_book(self) -> None:
        """Cash must not go blank after physical quotes age out post-bank-hours."""

        self.add("cash-physical", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:00:00Z", settlement="TODAY", form="PHYSICAL")
        self.add("cash-imam", instrument="COIN_IMAM", price=186_900, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:01:00Z", settlement="CASH", form="PHYSICAL", event_type="TRADE")
        self.add("tomorrow-paper", instrument="MELTED_GOLD_PRIVATE", price=81_000_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:19:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.connection.commit()

        imam = self.rate_at("IMAM", "CASH", at="2026-08-04T10:20:00Z")

        self.assertEqual((imam.status, imam.confidence), ("ESTIMATED", "LOW_PAPER_FALLBACK"))
        self.assertEqual(imam.underlying_source, "PRIVATE_PAPER_TOMORROW_CASH_BRIDGE")
        self.assertEqual(imam.estimated_project_price, 188_500)
        self.assertNotEqual(imam.reason, "NO_FRESH_MELTED")

    def test_unsettled_aggregate_paper_is_last_resort_low_confidence_only(self) -> None:
        """A live aggregate quote keeps preview useful without price authority."""

        self.add(
            "cash-imam-anchor",
            instrument="COIN_IMAM",
            price=186_900,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-04T10:01:00Z",
            settlement="CASH",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.add(
            "tomorrow-imam-anchor",
            instrument="COIN_IMAM",
            price=187_300,
            unit="PROJECT_THOUSAND_TOMAN",
            at="2026-08-04T10:01:00Z",
            settlement="TOMORROW",
            form="PHYSICAL",
            event_type="TRADE",
        )
        self.add(
            "unsettled-public-paper-anchor",
            instrument="MELTED_GOLD_AGGREGATE",
            price=80_300_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:00:00Z",
            settlement="UNKNOWN",
            form="PAPER_NORMAL",
            source_code="MELTED_AGGREGATE",
        )
        self.add(
            "unsettled-public-paper",
            instrument="MELTED_GOLD_AGGREGATE",
            price=81_000_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:09:30Z",
            settlement="UNKNOWN",
            form="PAPER_NORMAL",
            source_code="MELTED_AGGREGATE",
        )
        self.connection.commit()

        cash = self.rate("IMAM", "CASH")
        tomorrow = self.rate("IMAM", "TOMORROW")

        self.assertEqual((cash.status, cash.confidence), ("ESTIMATED", "LOW_PAPER_FALLBACK"))
        self.assertEqual(
            cash.underlying_source,
            "PUBLIC_PAPER_UNSPECIFIED_CASH_BRIDGE",
        )
        self.assertEqual(
            (tomorrow.status, tomorrow.confidence, tomorrow.underlying_source),
            (
                "ESTIMATED",
                "LOW_PAPER_FALLBACK",
                "PUBLIC_PAPER_UNSPECIFIED_TOMORROW_BRIDGE",
            ),
        )

    def test_settled_flow_outranks_unsettled_aggregate_paper(self) -> None:
        self.add(
            "settled-flow",
            instrument="MELTED_GOLD_FLOW",
            price=80_700_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:09:20Z",
            settlement="TOMORROW",
            form="PAPER_NORMAL",
            source_code="MELTED_FLOW",
        )
        self.add(
            "unsettled-aggregate",
            instrument="MELTED_GOLD_AGGREGATE",
            price=81_000_000,
            unit="TOMAN_PER_MESGHAL_750",
            at="2026-08-04T10:09:30Z",
            settlement="UNKNOWN",
            form="PAPER_NORMAL",
            source_code="MELTED_AGGREGATE",
        )
        self.connection.commit()

        rate = self.rate("QUARTER_LOW_DATE", "TOMORROW")

        self.assertEqual(rate.underlying_source, "PUBLIC_PAPER_TOMORROW")

    def test_paper_up_regime_only_widens_positive_side_with_a_bounded_interval(self) -> None:
        for index, price in enumerate((80_000_000, 80_020_000, 80_100_000, 80_400_000), start=6):
            self.add(f"paper-{index}", instrument="MELTED_GOLD_PRIVATE", price=price, unit="TOMAN_PER_MESGHAL_750", at=f"2026-08-04T10:{index:02d}:00Z", settlement="TOMORROW", form="PAPER_NORMAL", source_code="PRIVATE_GOLD_PAPER_MINUTE")
        self.connection.commit()
        low = self.rate("BAHAR", "TOMORROW")
        self.assertEqual(low.market_regime, "UP")
        self.assertGreaterEqual(low.upper_project_price - low.estimated_project_price, low.estimated_project_price - low.lower_project_price)
        self.assertLess(low.upper_project_price - low.lower_project_price, 5_000)

    def test_tomorrow_anchor_uses_same_form_paper_herat_basis(self) -> None:
        self.add("gold-old", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:00:00Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.add("gold-now", instrument="MELTED_GOLD_PRIVATE", price=81_000_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.add("imam", instrument="COIN_IMAM", price=186_900, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:01:00Z", settlement="TOMORROW", form="PHYSICAL", event_type="TRADE")
        self.add("herat-old", instrument="USD_HERAT", price=100_000, unit="TOMAN_PER_USD", at="2026-08-04T10:00:00Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.add("herat-now", instrument="USD_HERAT", price=102_000, unit="TOMAN_PER_USD", at="2026-08-04T10:09:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.connection.commit()
        imam = self.rate("IMAM", "TOMORROW")
        self.assertEqual(imam.estimated_project_price, 189_700)
        self.assertEqual(imam.herat_source, "HERAT_PAPER_TOMORROW")
        self.assertIn("HERAT_BASIS_BRIDGE", imam.method)
        self.assertGreater(imam.herat_basis_relative, 0.01)

    def test_herat_bridge_never_mixes_anchor_and_current_forms(self) -> None:
        self.add("gold-old", instrument="MELTED_GOLD_PRIVATE", price=80_300_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:00:00Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.add("gold-now", instrument="MELTED_GOLD_PRIVATE", price=81_000_000, unit="TOMAN_PER_MESGHAL_750", at="2026-08-04T10:09:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.add("imam", instrument="COIN_IMAM", price=186_900, unit="PROJECT_THOUSAND_TOMAN", at="2026-08-04T10:01:00Z", settlement="TOMORROW", form="PHYSICAL", event_type="TRADE")
        self.add("herat-today", instrument="USD_HERAT", price=100_000, unit="TOMAN_PER_USD", at="2026-08-04T10:00:00Z", settlement="TODAY", form="PAPER_NORMAL")
        self.add("herat-tomorrow", instrument="USD_HERAT", price=102_000, unit="TOMAN_PER_USD", at="2026-08-04T10:09:30Z", settlement="TOMORROW", form="PAPER_NORMAL")
        self.connection.commit()
        imam = self.rate("IMAM", "TOMORROW")
        self.assertEqual(imam.estimated_project_price, 188_500)
        self.assertNotIn("HERAT_BASIS_BRIDGE", imam.method)
        self.assertIsNone(imam.herat_source)


if __name__ == "__main__":
    unittest.main()
