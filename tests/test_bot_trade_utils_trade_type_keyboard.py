import unittest

from bot.callbacks import TradeActionCallback, TradeTypeCallback, TradeWizardActionCallback
from bot.handlers.trade_utils import get_trade_type_keyboard


class BotTradeUtilsTradeTypeKeyboardTests(unittest.TestCase):
    def test_get_trade_type_keyboard_builds_expected_buttons(self):
        keyboard = get_trade_type_keyboard()

        self.assertEqual(len(keyboard.inline_keyboard), 2)
        first_row = keyboard.inline_keyboard[0]
        self.assertEqual(first_row[0].text, "🟢 خرید")
        self.assertEqual(first_row[1].text, "🔴 فروش")
        self.assertEqual(first_row[0].callback_data, TradeTypeCallback(type="buy").pack())
        self.assertEqual(first_row[1].callback_data, TradeTypeCallback(type="sell").pack())
        self.assertEqual(keyboard.inline_keyboard[1][0].text, "❌ انصراف")
        self.assertEqual(
            keyboard.inline_keyboard[1][0].callback_data,
            TradeActionCallback(action="cancel").pack(),
        )

        edit_keyboard = get_trade_type_keyboard(return_to_review=True)
        self.assertEqual(
            edit_keyboard.inline_keyboard[1][0].callback_data,
            TradeWizardActionCallback(action="review").pack(),
        )


if __name__ == "__main__":
    unittest.main()
