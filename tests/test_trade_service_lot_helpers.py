import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.services.trade_service import (
    generate_default_lots,
    parse_lot_sizes_text,
    suggest_lot_combination,
    validate_lot_sizes,
)


class TradeServiceLotHelperTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            lot_min_size=5,
            lot_max_count=3,
            offer_min_quantity=5,
        )
        self.settings_patch = patch(
            "core.services.trade_service.get_trading_settings",
            return_value=self.settings,
        )
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()

    def test_suggest_lot_combination_returns_sorted_lots_when_sum_matches(self):
        self.assertEqual(suggest_lot_combination(34, [8, 16, 10]), [16, 10, 8])

    def test_suggest_lot_combination_adds_deficit_to_largest_lot(self):
        self.assertEqual(suggest_lot_combination(20, [5, 5, 5]), [10, 5, 5])

    def test_suggest_lot_combination_reduces_excess_across_multiple_lots(self):
        self.assertEqual(suggest_lot_combination(20, [12, 10, 8]), [8, 7, 5])

    def test_suggest_lot_combination_returns_none_for_unfixable_excess_or_invalid_input(self):
        self.assertIsNone(suggest_lot_combination(10, [6, 6, 6]))
        self.assertIsNone(suggest_lot_combination("bad-total", [10, 10]))
        self.assertIsNone(suggest_lot_combination(10, "10 10"))

    def test_generate_default_lots_uses_three_way_two_way_and_single_lot_branches(self):
        self.assertEqual(generate_default_lots(31), [11, 10, 10])
        self.assertEqual(generate_default_lots(11), [6, 5])
        self.assertEqual(generate_default_lots(7), [7])
        self.assertIsNone(generate_default_lots("7.5"))

    def test_validate_lot_sizes_rejects_too_many_and_too_small_lots(self):
        valid, error, suggested = validate_lot_sizes(20, [5, 5, 5, 5])
        self.assertFalse(valid)
        self.assertEqual(error, "❌ حداکثر 3 بخش مجاز است.")
        self.assertIsNone(suggested)

        valid, error, suggested = validate_lot_sizes(20, [10, 4, 6])
        self.assertFalse(valid)
        self.assertEqual(error, "❌ حداقل تعداد باید 5 باشد (بخش نامعتبر: 4)")
        self.assertIsNone(suggested)

    def test_validate_lot_sizes_returns_fixable_suggestion(self):
        valid, error, suggested = validate_lot_sizes(20, [5, 5, 5])

        self.assertFalse(valid)
        self.assertEqual(suggested, [10, 5, 5])
        self.assertIn("جمع ترکیب (15) با کل (20) برابر نیست", error)
        self.assertIn("💡 پیشنهاد: 10 5 5", error)

    def test_validate_lot_sizes_rejects_unfixable_sum_mismatch(self):
        valid, error, suggested = validate_lot_sizes(10, [6, 6, 6])

        self.assertFalse(valid)
        self.assertEqual(error, "❌ جمع ترکیب (18) با کل (10) برابر نیست.")
        self.assertIsNone(suggested)

    def test_validate_lot_sizes_accepts_exact_valid_combination(self):
        valid, error, suggested = validate_lot_sizes(20, [10, 5, 5])

        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertEqual(suggested, [10, 5, 5])

    def test_parse_lot_sizes_text_parses_and_sorts_valid_input(self):
        valid, error, lots = parse_lot_sizes_text("10 5 12")

        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertEqual(lots, [12, 10, 5])

    def test_parse_lot_sizes_text_rejects_non_string_blank_and_invalid_values(self):
        samples = [
            (None, "❌ ورودی باید متن باشد."),
            ("   ", "❌ لطفاً ترکیب را وارد کنید."),
            ("10 x", '❌ "x" یک عدد معتبر نیست.'),
            ("10 -1", '❌ "-1" یک عدد معتبر نیست.'),
        ]

        for raw_value, expected_error in samples:
            with self.subTest(raw_value=raw_value):
                valid, error, lots = parse_lot_sizes_text(raw_value)
                self.assertFalse(valid)
                self.assertEqual(error, expected_error)
                self.assertIsNone(lots)


if __name__ == "__main__":
    unittest.main()