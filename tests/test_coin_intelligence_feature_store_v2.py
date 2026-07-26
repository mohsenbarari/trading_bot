from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from core.market_intelligence.feature_store_v2 import (
    derive_feature_context_v2,
    live_offer_age_weight,
)


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def row(
    minutes: int,
    *,
    settlement: str,
    price: int,
    outcome: int | None = None,
    quality_weight: float | None = None,
) -> dict:
    return {
        "run_as_of_utc": NOW + timedelta(minutes=minutes),
        "settlement": settlement,
        "offer_price": price,
        "intrinsic_toman": 170_000_000,
        "outcome_price": outcome,
        "outcome_occurred_at_utc": (
            NOW + timedelta(minutes=minutes, seconds=30)
            if outcome is not None
            else None
        ),
        "label_status": "UNREVIEWED",
        "quality_realtime_weight": quality_weight,
        "quality_training_weight": quality_weight,
        "training_eligible": False,
        "regime_label": "RANGE",
    }


class FeatureStoreV2Tests(unittest.TestCase):
    def test_outcome_wins_but_future_and_quality_zero_are_excluded(self) -> None:
        result = derive_feature_context_v2(
            [
                row(-4, settlement="CASH", price=180_000, outcome=181_000),
                row(
                    -3,
                    settlement="CASH",
                    price=182_000,
                    quality_weight=0.0,
                ),
                row(0, settlement="CASH", price=999_999),
            ],
            cutoff_utc=NOW,
            target_settlement="CASH",
        )

        self.assertEqual(len(result["same_market_history"]), 1)
        history = result["same_market_history"][0]
        self.assertEqual(history["source_kind"], "CONFIRMED_TRADE")
        self.assertAlmostEqual(
            history["bubble_ratio"],
            181_000_000 / 170_000_000 - 1,
        )

    def test_future_outcome_cannot_replace_prior_offer(self) -> None:
        value = row(-2, settlement="CASH", price=180_000)
        value["outcome_price"] = 190_000
        value["outcome_occurred_at_utc"] = NOW + timedelta(minutes=2)

        result = derive_feature_context_v2(
            [value],
            cutoff_utc=NOW,
            target_settlement="CASH",
        )

        self.assertEqual(
            result["same_market_history"][0]["source_kind"],
            "UNREVIEWED_OFFER",
        )
        self.assertAlmostEqual(
            result["same_market_history"][0]["bubble_ratio"],
            180_000_000 / 170_000_000 - 1,
        )

    def test_five_paired_observations_create_strictly_prior_basis(self) -> None:
        rows = []
        for index in range(5):
            minute = -4
            rows.append(
                row(
                    minute,
                    settlement="CASH",
                    price=181_000 + index * 100,
                )
            )
            rows.append(
                row(
                    minute,
                    settlement="TOMORROW",
                    price=180_000 + index * 100,
                )
            )
        result = derive_feature_context_v2(
            rows,
            cutoff_utc=NOW,
            target_settlement="CASH",
        )

        self.assertEqual(result["settlement_basis"]["status"], "OBSERVED")
        self.assertEqual(result["settlement_basis"]["pair_count"], 5)
        self.assertEqual(
            result["settlement_basis"]["counterpart_settlement"],
            "TOMORROW",
        )

    def test_live_offer_weight_reaches_zero_after_five_minutes(self) -> None:
        self.assertEqual(live_offer_age_weight(0), 1.0)
        self.assertAlmostEqual(live_offer_age_weight(299), 0.335555, places=5)
        self.assertEqual(live_offer_age_weight(300), 0.0)

    def test_confirmed_trade_has_more_live_weight_than_fresh_offer(
        self,
    ) -> None:
        result = derive_feature_context_v2(
            [
                row(
                    -1,
                    settlement="CASH",
                    price=180_000,
                    outcome=181_000,
                    quality_weight=1.0,
                ),
                row(
                    -1,
                    settlement="CASH",
                    price=180_500,
                    quality_weight=1.0,
                ),
            ],
            cutoff_utc=NOW,
            target_settlement="CASH",
        )

        by_kind = {
            item["source_kind"]: item["source_weight"]
            for item in result["same_market_history"]
        }
        self.assertGreater(
            by_kind["CONFIRMED_TRADE"],
            by_kind["UNREVIEWED_OFFER"],
        )

    def test_training_zero_does_not_remove_valid_realtime_trade(
        self,
    ) -> None:
        value = row(
            -1,
            settlement="CASH",
            price=180_000,
            outcome=181_000,
        )
        value.update(
            {
                "label_status": "REVIEWED",
                "training_eligible": True,
                "quality_realtime_weight": 1.0,
                "quality_training_weight": 0.0,
            }
        )

        result = derive_feature_context_v2(
            [value],
            cutoff_utc=NOW,
            target_settlement="CASH",
        )

        self.assertEqual(len(result["same_market_history"]), 1)
        self.assertFalse(
            result["same_market_history"][0]["training_eligible"]
        )


if __name__ == "__main__":
    unittest.main()
