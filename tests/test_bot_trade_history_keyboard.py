import unittest

from bot.handlers.trade_history import get_trade_history_keyboard


class BotTradeHistoryKeyboardTests(unittest.TestCase):
    def test_get_trade_history_keyboard_builds_export_filter_and_back_buttons(self):
        markup = get_trade_history_keyboard(17)

        self.assertEqual(len(markup.inline_keyboard), 3)
        self.assertIn("Excel", markup.inline_keyboard[0][0].text)
        self.assertIn("PDF", markup.inline_keyboard[0][1].text)
        self.assertIn("بازگشت", markup.inline_keyboard[2][0].text)


if __name__ == "__main__":
    unittest.main()