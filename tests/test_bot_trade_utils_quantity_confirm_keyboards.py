import unittest

from bot.callbacks import QuantityCallback, TradeActionCallback
from bot.handlers.trade_utils import get_confirm_keyboard, get_quantity_keyboard


class BotTradeUtilsQuantityConfirmKeyboardTests(unittest.TestCase):
    def test_get_quantity_keyboard_builds_quick_manual_and_back_buttons(self):
        keyboard = get_quantity_keyboard()

        self.assertEqual([button.text for button in keyboard.inline_keyboard[0]], ["10", "20", "30", "40", "50"])
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, QuantityCallback(value="10").pack())
        self.assertEqual(keyboard.inline_keyboard[1][0].callback_data, QuantityCallback(value="manual").pack())
        self.assertEqual(
            keyboard.inline_keyboard[2][0].callback_data,
            TradeActionCallback(action="back_to_commodity").pack(),
        )

    def test_get_confirm_keyboard_builds_confirm_and_cancel_buttons(self):
        keyboard = get_confirm_keyboard()

        self.assertEqual(len(keyboard.inline_keyboard), 2)
        self.assertEqual(
            keyboard.inline_keyboard[0][0].callback_data,
            TradeActionCallback(action="confirm").pack(),
        )
        self.assertEqual(
            keyboard.inline_keyboard[1][0].callback_data,
            TradeActionCallback(action="cancel").pack(),
        )


if __name__ == "__main__":
    unittest.main()