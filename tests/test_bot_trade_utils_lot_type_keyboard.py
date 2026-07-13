import unittest

from bot.callbacks import LotTypeCallback, TradeActionCallback
from bot.handlers.trade_utils import get_lot_type_keyboard


class BotTradeUtilsLotTypeKeyboardTests(unittest.TestCase):
    def test_get_lot_type_keyboard_builds_expected_buttons(self):
        keyboard = get_lot_type_keyboard()

        self.assertEqual(len(keyboard.inline_keyboard), 2)
        first_row = keyboard.inline_keyboard[0]
        self.assertEqual([button.text for button in first_row], ["📦 یکجا", "خُرد"])
        self.assertEqual(first_row[0].callback_data, LotTypeCallback(type="wholesale").pack())
        self.assertEqual(first_row[1].callback_data, LotTypeCallback(type="retail").pack())
        self.assertEqual(
            keyboard.inline_keyboard[1][0].callback_data,
            TradeActionCallback(action="back_to_quantity").pack(),
        )


if __name__ == "__main__":
    unittest.main()
