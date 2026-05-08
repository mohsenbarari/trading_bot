import unittest

from bot.callbacks import TradeTypeCallback
from bot.handlers.trade_utils import get_trade_type_keyboard


class BotTradeUtilsTradeTypeKeyboardTests(unittest.TestCase):
    def test_get_trade_type_keyboard_builds_expected_buttons(self):
        keyboard = get_trade_type_keyboard()

        self.assertEqual(len(keyboard.inline_keyboard), 2)
        first_row = keyboard.inline_keyboard[0]
        self.assertEqual(first_row[0].text, "🟢 خریدارم")
        self.assertIn("فروشنده", first_row[1].text)
        self.assertEqual(first_row[0].callback_data, TradeTypeCallback(type="buy").pack())
        self.assertEqual(first_row[1].callback_data, TradeTypeCallback(type="sell").pack())
        self.assertEqual(keyboard.inline_keyboard[1][0].callback_data, "panel_back")


if __name__ == "__main__":
    unittest.main()