import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.services.trade_service import (
    _ensure_int,
    _ensure_int_list,
    build_lot_unavailable_suggestion_payload,
    get_available_trade_amounts,
    suggest_lot_combination,
    validate_lot_sizes,
    validate_offer_trade_amount,
    validate_price,
    validate_quantity,
)


class TradeServiceValidationAndPayloadTests(unittest.TestCase):
    def setUp(self):
        self.settings = SimpleNamespace(
            lot_min_size=5,
            lot_max_count=3,
            offer_min_quantity=5,
            offer_max_quantity=50,
        )
        self.settings_patch = patch(
            "core.services.trade_service.get_trading_settings",
            return_value=self.settings,
        )
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()

    def test_ensure_int_rejects_fractional_float_invalid_string_and_object(self):
        self.assertEqual(_ensure_int(7.0, "quantity"), 7)

        with self.assertRaisesRegex(TypeError, "quantity باید یک عدد صحیح باشد، نه اعشاری"):
            _ensure_int(7.5, "quantity")

        with self.assertRaisesRegex(TypeError, "quantity باید یک عدد صحیح باشد"):
            _ensure_int("bad", "quantity")

        with self.assertRaisesRegex(TypeError, "quantity باید یک عدد صحیح باشد"):
            _ensure_int(object(), "quantity")

    def test_ensure_int_list_rejects_non_list_inputs(self):
        with self.assertRaisesRegex(TypeError, "lots باید یک لیست باشد"):
            _ensure_int_list("10 20", "lots")

    def test_suggest_lot_combination_returns_none_for_empty_lot_list(self):
        self.assertIsNone(suggest_lot_combination(10, []))

    def test_validate_lot_sizes_returns_type_error_message_for_invalid_inputs(self):
        valid, error, suggested = validate_lot_sizes(object(), [5, 5])

        self.assertFalse(valid)
        self.assertEqual(error, "❌ total باید یک عدد صحیح باشد")
        self.assertIsNone(suggested)

    def test_get_available_trade_amounts_handles_invalid_nonpositive_and_retail_filters(self):
        self.assertEqual(get_available_trade_amounts("bad", 10, False, [5]), [])
        self.assertEqual(get_available_trade_amounts(20, 0, False, [5]), [])
        self.assertEqual(get_available_trade_amounts(20, 12, True, [5, 7]), [12])
        self.assertEqual(get_available_trade_amounts(20, 12, False, "bad"), [])
        self.assertEqual(get_available_trade_amounts(20, 12, False, [10, 10, 0, 15, 5]), [12, 10, 5])

    def test_validate_offer_trade_amount_covers_error_paths_and_success(self):
        self.assertEqual(
            validate_offer_trade_amount(20, 10, False, [5], "bad"),
            (False, "requested_amount باید یک عدد صحیح باشد", 0, []),
        )

        self.assertEqual(
            validate_offer_trade_amount(20, 10, False, [5], 0),
            (False, "تعداد معامله نامعتبر است.", 0, []),
        )

        self.assertEqual(
            validate_offer_trade_amount(20, 10, False, [5], 11),
            (False, "تعداد درخواستی بیشتر از موجودی است. موجودی: 10", 11, []),
        )

        self.assertEqual(
            validate_offer_trade_amount(20, 10, True, [5], 5),
            (False, "تعداد درخواستی بیشتر از موجودی است. موجودی: 10", 5, [10]),
        )

        self.assertEqual(
            validate_offer_trade_amount(20, 10, False, [4, 6], 5),
            (False, "این لات دیگر موجود نیست.", 5, [10, 6, 4]),
        )

        self.assertEqual(
            validate_offer_trade_amount(20, 10, False, [6, 4], 6),
            (True, "", 6, [10, 6, 4]),
        )

    def test_build_lot_unavailable_suggestion_payload_formats_sell_payload(self):
        payload = build_lot_unavailable_suggestion_payload(
            offer_id="7",
            offer_public_id="ofr_trade_service_7",
            requested_amount="10",
            offer_type=SimpleNamespace(value="sell"),
            commodity_name="  طلای آب شده  ",
            price="75800",
            remaining_quantity="20",
            available_amounts=[10, 5],
        )

        self.assertEqual(payload["offer_id"], 7)
        self.assertEqual(payload["offer_public_id"], "ofr_trade_service_7")
        self.assertEqual(payload["requested_amount"], 10)
        self.assertEqual(payload["offer_type"], "sell")
        self.assertEqual(payload["offer_type_label"], "فروش")
        self.assertEqual(payload["offer_type_emoji"], "🔴")
        self.assertEqual(payload["commodity_name"], "طلای آب شده")
        self.assertEqual(payload["lot_summary"], "10 + 5")
        self.assertEqual(payload["available_lots_text"], "10 عدد، 5 عدد")
        self.assertEqual(payload["intro_text"], "بخش 10 عددی که انتخاب کرده بودید لحظاتی قبل توسط کاربر دیگری انجام شد.")
        self.assertEqual(payload["detail"], "بخش انتخابی شما لحظاتی قبل انجام شد.")
        self.assertIn("اگر مایل هستید", payload["message"])
        self.assertIn("🔴فروش طلای آب شده 20 عدد 75,800", payload["offer_summary"])

    def test_build_lot_unavailable_suggestion_payload_handles_unknown_offer_type_and_no_lots(self):
        payload = build_lot_unavailable_suggestion_payload(
            offer_id=7,
            requested_amount=3,
            offer_type=SimpleNamespace(value="mystery"),
            commodity_name="   ",
            price=50000,
            remaining_quantity=8,
            available_amounts=[],
        )

        self.assertEqual(payload["offer_type"], "")
        self.assertEqual(payload["offer_type_label"], "")
        self.assertEqual(payload["offer_type_emoji"], "")
        self.assertEqual(payload["commodity_name"], "کالا")
        self.assertEqual(payload["lot_summary"], "ندارد")
        self.assertEqual(payload["available_lots_text"], "ندارد")
        self.assertIn("دکمه فعالی ندارد", payload["message"])

    def test_validate_quantity_covers_invalid_below_above_and_valid_values(self):
        self.assertEqual(validate_quantity(7.5), (False, "❌ quantity باید یک عدد صحیح باشد، نه اعشاری"))
        self.assertEqual(validate_quantity(4), (False, "❌ تعداد باید حداقل 5 باشد."))
        self.assertEqual(validate_quantity(51), (False, "❌ تعداد نمی‌تواند بیشتر از 50 باشد."))
        self.assertEqual(validate_quantity(25), (True, ""))

    def test_validate_price_covers_invalid_nonpositive_length_and_valid_values(self):
        self.assertEqual(validate_price("bad"), (False, "❌ price باید یک عدد صحیح باشد"))
        self.assertEqual(validate_price(0), (False, "❌ قیمت باید بزرگ‌تر از صفر باشد."))
        self.assertEqual(validate_price(9999), (False, "❌ قیمت باید 5 یا 6 رقم باشد (مثال: 75800 یا 758000)"))
        self.assertEqual(validate_price(75800), (True, ""))


if __name__ == "__main__":
    unittest.main()
