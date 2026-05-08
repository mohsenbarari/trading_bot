import unittest
from unittest.mock import patch

from bot.handlers.trade_create import _get_offer_suggestion, has_trade_indicator


class BotTradeCreateTextHelperTests(unittest.TestCase):
    def test_get_offer_suggestion_returns_contextual_hints(self):
        quantity_hint = _get_offer_suggestion("خ ربع 30 75800", "تعداد نامعتبر است")
        self.assertIn("30تا", quantity_hint)

        with patch("core.trading_settings.get_trading_settings", return_value=type("TS", (), {"offer_min_quantity": 5, "offer_max_quantity": 50})()):
            bounds_hint = _get_offer_suggestion("خ ربع 1تا 75800", "حداقل مجاز")
        self.assertIn("5 تا 50", bounds_hint)

    def test_has_trade_indicator_detects_valid_markers_only(self):
        self.assertTrue(has_trade_indicator("خ ربع 30تا 75800"))
        self.assertTrue(has_trade_indicator("فروش نیم 50عدد 758000: فقط نقدی"))
        self.assertFalse(has_trade_indicator("این پیام عادی است"))
        self.assertFalse(has_trade_indicator(""))


if __name__ == "__main__":
    unittest.main()