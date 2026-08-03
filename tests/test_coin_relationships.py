from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.coin_relationships import (
    ConfirmedCoinTrade,
    build_coin_intrinsic_label,
)
from core.market_intelligence.melted_relationships import MeltedMarketEvent


UTC = timezone.utc
NOW = datetime(2026, 8, 3, 8, 0, tzinfo=UTC)


def melted(minutes: int, market: str, price: float) -> MeltedMarketEvent:
    return MeltedMarketEvent(
        observed_at_utc=NOW + timedelta(minutes=minutes),
        market_key=market,
        event_type="TRADE",
        side="BUY",
        price=price,
    )


class CoinRelationshipTests(unittest.TestCase):
    def test_uses_prior_tomorrow_melted_and_explicit_imam_formula(self):
        trade = ConfirmedCoinTrade(NOW, "امام", "TOMORROW", "PHYSICAL", 180_240.0)
        label = build_coin_intrinsic_label(
            trade,
            events_by_market={
                "PAPER:TOMORROW:NORMAL": [
                    melted(-1, "PAPER:TOMORROW:NORMAL", 80_000_000.0)
                ]
            },
            max_anchor_age=timedelta(minutes=3),
        )
        self.assertIsNotNone(label)
        assert label is not None
        self.assertEqual(label.intrinsic_project_price, 180_240.0)
        self.assertEqual(label.bubble_ratio, 0.0)

    def test_never_uses_a_same_or_future_melted_event(self):
        trade = ConfirmedCoinTrade(NOW, "ربع بهار", "TOMORROW", "PHYSICAL", 45_000.0)
        label = build_coin_intrinsic_label(
            trade,
            events_by_market={
                "PAPER:TOMORROW:NORMAL": [
                    melted(0, "PAPER:TOMORROW:NORMAL", 80_000_000.0),
                    melted(1, "PAPER:TOMORROW:NORMAL", 80_000_000.0),
                ]
            },
            max_anchor_age=timedelta(minutes=3),
        )
        self.assertIsNone(label)

    def test_cash_falls_back_to_today_paper_without_conditional_mix(self):
        trade = ConfirmedCoinTrade(NOW, "نیم بهار", "CASH", "PHYSICAL", 90_120.0)
        label = build_coin_intrinsic_label(
            trade,
            events_by_market={
                "PAPER:TODAY:NORMAL": [
                    melted(-2, "PAPER:TODAY:NORMAL", 80_000_000.0)
                ],
                "PHYSICAL:TODAY:CONDITIONAL": [
                    melted(-1, "PHYSICAL:TODAY:CONDITIONAL", 70_000_000.0)
                ],
            },
            max_anchor_age=timedelta(minutes=3),
        )
        self.assertIsNotNone(label)
        assert label is not None
        self.assertEqual(label.melted_anchor_market, "PAPER:TODAY:NORMAL")


if __name__ == "__main__":
    unittest.main()
