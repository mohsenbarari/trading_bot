import unittest
from unittest.mock import patch

from bot.handlers.trade_create import (
    _get_offer_suggestion,
    has_trade_indicator,
    looks_like_text_offer,
)


class BotTradeCreateTextHelperTests(unittest.TestCase):
    def test_get_offer_suggestion_returns_contextual_hints(self):
        quantity_hint = _get_offer_suggestion("خ ربع 30 75800", "تعداد نامعتبر است")
        self.assertIn("30تا", quantity_hint)

        with patch("core.trading_settings.get_trading_settings", return_value=type("TS", (), {"offer_min_quantity": 5, "offer_max_quantity": 50})()):
            bounds_hint = _get_offer_suggestion("خ ربع 1تا 75800", "حداقل مجاز")
        self.assertIn("5 تا 50", bounds_hint)

    def test_has_trade_indicator_detects_valid_markers_only(self):
        self.assertTrue(has_trade_indicator("خ ربع 30تا 75800"))
        self.assertTrue(has_trade_indicator("خف ربع 30تا 75"))
        self.assertTrue(has_trade_indicator("خ‌ف ربع 30تا 75"))
        self.assertTrue(has_trade_indicator("فف پک 105"))
        self.assertTrue(has_trade_indicator("ف‌ف پک 105"))
        self.assertTrue(has_trade_indicator("فروش نیم 50عدد 758000: فقط نقدی"))
        self.assertTrue(has_trade_indicator("امام 30تا 75800 خ ن"))
        self.assertTrue(has_trade_indicator("ربع 30تا فروش نقد فردا 75800"))
        self.assertFalse(has_trade_indicator("امام 30تا 75800: خ ن"))
        self.assertFalse(has_trade_indicator("تخفیف ویژه"))
        self.assertFalse(has_trade_indicator("این پیام عادی است"))
        self.assertFalse(has_trade_indicator(""))

    def test_looks_like_text_offer_keeps_offer_shaped_input_out_of_silent_fallback(self):
        self.assertTrue(looks_like_text_offer("امام 30تا 75800"))
        self.assertTrue(looks_like_text_offer("امام ۳۰ عدد ۷۵۸۰۰: خ"))
        self.assertTrue(looks_like_text_offer("خ ربع 30تا 75800"))
        self.assertTrue(looks_like_text_offer("خف ربع 30تا 75"))
        self.assertTrue(looks_like_text_offer("خف پک 105"))
        self.assertTrue(looks_like_text_offer("فف نیم 20تا 95"))
        self.assertFalse(looks_like_text_offer("این پیام عادی است"))
        self.assertFalse(looks_like_text_offer("سفارش 30 عدد"))


if __name__ == "__main__":
    unittest.main()
