from __future__ import annotations

import unittest

from core.market_intelligence.quality import evaluate_offer_quality


def regime(label: str = "RANGE", direction: float = 0.0) -> dict:
    return {
        "label": label,
        "direction_score": direction,
        "confidence": 0.9,
        "agreement_score": 0.9,
        "disagreement_flag": False,
    }


class CoinIntelligenceQualityV2Tests(unittest.TestCase):
    def test_sell_below_lowest_buy_is_excluded_in_range(self) -> None:
        result = evaluate_offer_quality(
            side="SELL",
            price_project=179_000,
            lowest_active_buy=180_000,
            highest_active_sell=182_000,
            regime_v2=regime(),
            structural_reference_project=181_000,
        )

        self.assertEqual(result.decision, "EXCLUDE")
        self.assertEqual(result.realtime_weight, 0.0)
        self.assertEqual(result.training_weight, 0.0)
        self.assertIn(
            "SELL_BELOW_LOWEST_ACTIVE_BUY",
            result.reason_codes,
        )

    def test_buy_above_highest_sell_is_excluded_in_range(self) -> None:
        result = evaluate_offer_quality(
            side="BUY",
            price_project=183_000,
            lowest_active_buy=180_000,
            highest_active_sell=182_000,
            regime_v2=regime(),
        )

        self.assertEqual(result.decision, "EXCLUDE")
        self.assertEqual(result.training_weight, 0.0)

    def test_independent_underlying_direction_allows_reduced_shadow_weight(
        self,
    ) -> None:
        result = evaluate_offer_quality(
            side="SELL",
            price_project=179_000,
            lowest_active_buy=180_000,
            highest_active_sell=182_000,
            regime_v2=regime("DOWN", -0.8),
            structural_reference_project=180_000,
        )

        self.assertEqual(result.decision, "INCLUDE_SHADOW")
        self.assertGreater(result.realtime_weight, 0)
        self.assertLess(result.realtime_weight, 1)

    def test_discontinuity_is_quarantined_not_corrected(self) -> None:
        result = evaluate_offer_quality(
            side="SELL",
            price_project=160_000,
            lowest_active_buy=None,
            highest_active_sell=None,
            regime_v2=regime(),
            structural_reference_project=180_000,
        )

        self.assertEqual(result.decision, "REVIEW_REQUIRED")
        self.assertTrue(result.review_required)
        self.assertEqual(result.realtime_weight, 0.0)


if __name__ == "__main__":
    unittest.main()
