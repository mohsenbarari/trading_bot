from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.melted_relationships import (
    MeltedMarketEvent,
    RelationshipObservation,
    build_melted_window_features,
    iran_calendar_features,
    market_key_from_fields,
    rank_relationships,
)


UTC = timezone.utc
NOW = datetime(2026, 8, 2, 8, 0, tzinfo=UTC)


def event(minutes: int, kind: str, side: str, price: float, quantity=None):
    return MeltedMarketEvent(
        observed_at_utc=NOW + timedelta(minutes=minutes),
        market_key="PAPER:TOMORROW:NORMAL",
        event_type=kind,
        side=side,
        price=price,
        quantity=quantity,
    )


class MeltedRelationshipTests(unittest.TestCase):
    def test_market_keys_keep_variant_and_conditional_separate(self):
        self.assertEqual(
            market_key_from_fields(
                trade_form="PAPER",
                settlement="TOMORROW",
                market_label="آبشده کانال جدید کاغذی معکوس",
            ),
            ("PAPER:TOMORROW:REVERSE", False),
        )
        self.assertEqual(
            market_key_from_fields(
                trade_form="PHYSICAL",
                settlement="TODAY",
                market_label="آبشده کانال جدید نقد حاضر شرطی",
            ),
            ("PHYSICAL:TODAY:CONDITIONAL", True),
        )
        self.assertEqual(
            market_key_from_fields(
                trade_form="PAPER_SWIM",
                settlement="TOMORROW",
                market_label="PRIVATE_GOLD_PAPER_SWIM",
            ),
            ("PAPER:TOMORROW:SWIM", False),
        )

    def test_window_features_exclude_future_event(self):
        rows = [
            event(-4, "OFFER", "BUY", 100),
            event(-3, "TRADE", "BUY", 101, 2),
            event(-2, "OFFER", "BUY", 102),
            event(1, "TRADE", "SELL", 80, 9),
        ]
        features = build_melted_window_features(
            rows, as_of_utc=NOW, window=timedelta(minutes=5)
        )
        self.assertEqual(features["event_count"], 3)
        self.assertEqual(features["trade_count"], 1)
        self.assertEqual(features["latest_directional_side"], "BUY")
        self.assertEqual(features["latest_directional_run"], 3)
        self.assertEqual(features["confirmed_quantity_sum"], 2)
        self.assertAlmostEqual(features["price_change_bps"], 200.0)

    def test_calendar_is_explicitly_tehran_and_jalali(self):
        value = iran_calendar_features(datetime(2026, 8, 2, 20, 45, tzinfo=UTC))
        self.assertEqual(value["timezone"], "Asia/Tehran")
        self.assertEqual(value["tehran_hour"], 0)
        self.assertGreater(value["jalali_year"], 1400)

    def test_relationships_require_strictly_future_targets_and_rank(self):
        with self.assertRaises(ValueError):
            RelationshipObservation("x", "y", NOW, NOW, 1.0, 1.0)
        rows = [
            RelationshipObservation(
                "paper_normal_trade_imbalance",
                "physical_tomorrow_return_5m",
                NOW + timedelta(minutes=index),
                NOW + timedelta(minutes=index + 5),
                float(index),
                float(index * 2),
            )
            for index in range(4)
        ]
        ranked = rank_relationships(rows, min_samples=4)
        self.assertEqual(len(ranked), 1)
        self.assertEqual(ranked[0]["pearson_correlation"], 1.0)
