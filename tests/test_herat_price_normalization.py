from __future__ import annotations

from decimal import Decimal
import unittest

from core.market_intelligence.herat_price_normalization import normalize_herat_price


class HeratPriceNormalizationTests(unittest.TestCase):
    def test_clipped_leading_digits_are_reconstructed_from_prior_range(self) -> None:
        decision = normalize_herat_price(
            Decimal("85600"),
            strictly_prior_same_book_prices=(185_200, 185_350, 185_500),
        )
        self.assertTrue(decision.adjusted)
        self.assertEqual(decision.price, Decimal("185600"))
        self.assertEqual(decision.method, "RECONSTRUCTED_FROM_STRICTLY_PRIOR_RANGE")

    def test_literal_lower_market_regime_is_not_given_a_fixed_increment(self) -> None:
        decision = normalize_herat_price(
            Decimal("71500"),
            strictly_prior_same_book_prices=(71_400, 71_550, 71_700),
        )
        self.assertFalse(decision.adjusted)
        self.assertEqual(decision.price, Decimal("71500"))

    def test_insufficient_or_nonmatching_range_fails_closed(self) -> None:
        insufficient = normalize_herat_price(
            85_600,
            strictly_prior_same_book_prices=(185_200, 185_400),
        )
        unrelated = normalize_herat_price(
            85_600,
            strictly_prior_same_book_prices=(110_000, 110_100, 110_200),
        )
        self.assertFalse(insufficient.adjusted)
        self.assertFalse(unrelated.adjusted)
        self.assertEqual(insufficient.price, Decimal("85600"))
        self.assertEqual(unrelated.price, Decimal("85600"))


if __name__ == "__main__":
    unittest.main()
